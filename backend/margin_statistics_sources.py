"""Finite, resumable Package 1 source collection. Never touches strategy stores."""
from pathlib import Path
from datetime import date,timedelta,datetime
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from urllib.parse import urlparse,parse_qs,urlencode
import requests,time,threading,hashlib,json,gzip,os,fcntl,shutil,argparse
from . import db,margin_statistics as m
from .alpha_beta_sources import month_ranges,parse_cash_events
_LOCKS={k:threading.Lock() for k in ['twse','tpex']};_LAST={k:0. for k in _LOCKS};_BLOCKED={}
class SourcePaused(ValueError):pass
def limited(url):
 host='tpex' if 'tpex.org' in url else 'twse'
 with _LOCKS[host]:
  if host in _BLOCKED:raise SourcePaused('本輪官方來源已暫停：'+_BLOCKED[host]['reason']+'；此日期未下載')
  time.sleep(max(0,.45-(time.monotonic()-_LAST[host])));_LAST[host]=time.monotonic()
def record_paused_response(url,response):
 """Keep public response evidence without replacing any valid source snapshot."""
 raw=response.content
 if not isinstance(raw,bytes):return None
 try:
  root=m.folder()/'source-errors';root.mkdir(parents=True,exist_ok=True)
  digest=hashlib.sha256(raw).hexdigest();body=root/(digest+'.response.gz')
  if not body.exists():body.write_bytes(gzip.compress(raw))
  stamp=m.now();identity=hashlib.sha256((url+stamp+digest).encode()).hexdigest()
  m.atomic(root/(identity+'.json'),{'url':url,'observed_at':stamp,'status':response.status_code,'content_type':response.headers.get('Content-Type'),'bytes':len(raw),'sha256':digest,'raw_file':body.name})
  return identity
 except OSError:return None

def fetch(url,validate,force=False):
 key=hashlib.sha256(url.encode()).hexdigest();observed=m.now()
 if not force:
  if (m.folder()/'sources.sqlite3').exists():
   with m.connect() as c:
    row=c.execute('SELECT raw_path,retrieved_at FROM snapshots WHERE url=? ORDER BY retrieved_at DESC LIMIT 1',(url,)).fetchone()
    if row is None:
     ex=c.execute('SELECT payload,retrieved_at FROM extras WHERE url=? ORDER BY retrieved_at DESC LIMIT 1',(url,)).fetchone()
     if ex is not None:row=(json.loads(ex[0])['raw_path'],ex[1])
   if row is not None:
    try:
     raw=gzip.decompress((m.folder()/row[0]).read_bytes());v=json.loads(raw);validate(v);return v,raw,row[1],observed,url
    except (OSError,ValueError,KeyError):pass
  for p,meta in [(db.DATA/'raw'/(key+'.gz'),db.DATA/'raw'/(key+'.json')),(db.DATA/'alpha_beta/raw'/(key+'.json'),db.DATA/'alpha_beta/raw'/(key+'.meta'))]:
   if not p.exists():continue
   try:
    raw=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes();v=json.loads(raw);validate(v)
    info=json.loads(meta.read_text()) if meta.exists() else {};return v,raw,info.get('fetched_at') or observed,observed,url
   except (OSError,ValueError,KeyError):pass
 errors=[]
 for attempt in range(3):
  actual=url
  if attempt==1 and 'twse.com.tw/rwd/zh/' in url and '/marginTrading/' not in url:
   endpoint=url.split('/rwd/zh/')[1].split('?')[0].split('/')[-1];q=parse_qs(urlparse(url).query);base='fund' if endpoint=='MI_QFIIS' else 'exchangeReport';params={'response':'json',**{k:v[0] for k,v in q.items() if k!='response'}};actual=f'https://www.twse.com.tw/{base}/{endpoint}?'+urlencode(params)
  try:
   limited(actual);r=requests.get(actual,timeout=(8,25));
   if r.status_code in [403,428,429]:
    record_paused_response(actual,r)
    host='tpex' if 'tpex.org' in actual else 'twse'
    with _LOCKS[host]:_BLOCKED[host]={'reason':'官方防護或限流 HTTP '+str(r.status_code),'observed_at':m.now()}
    raise SourcePaused('官方來源暫停：'+_BLOCKED[host]['reason']+'；未嘗試繞過防護')
   r.raise_for_status();raw=r.content
   try:v=json.loads(raw)
   except (ValueError,UnicodeDecodeError):
    record_paused_response(actual,r)
    host='tpex' if 'tpex.org' in actual else 'twse'
    with _LOCKS[host]:_BLOCKED[host]={'reason':'官方回傳非JSON／維護頁，保留既有資料','observed_at':m.now()}
    raise SourcePaused('官方回傳非JSON／維護頁；本輪來源已停止，未切換主機重試')
   validate(v);return v,raw,m.now(),None,actual
  except (requests.RequestException,ValueError,KeyError,IndexError,TypeError) as e:
   if isinstance(e,SourcePaused):raise
   if isinstance(e,requests.HTTPError) and e.response is not None and e.response.status_code in [428,429,503]:
    host='tpex' if 'tpex.org' in actual else 'twse'
    with _LOCKS[host]:_LAST[host]=time.monotonic()+5
   errors.append(str(e)[:180]);time.sleep(.4*(attempt+1))
 raise ValueError('公開來源未通過核對：'+'；'.join(errors))
def urls(day):
 compact=day.replace('-','');slash=day.replace('-','/')
 return [('TWSE','credit',f'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&date={compact}&selectType=ALL'),('TWSE','quotes',f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={compact}&type=ALLBUT0999&response=json'),('TWSE','shares',f'https://www.twse.com.tw/fund/MI_QFIIS?response=json&date={compact}&selectType=ALLBUT0999'),('TPEX','credit',f'https://www.tpex.org.tw/www/zh-tw/margin/balance?date={slash}&response=json'),('TPEX','quotes',f'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={slash}&response=json')]
def validate(v,market,kind,day):
 return m.parse_credit(v,market,day) if kind=='credit' else m.parse_quotes(v,market,day) if kind=='quotes' else m.parse_shares(v,day)
def calendar_rows(v,month,start,end):
 m.check(v,month)
 if not v.get('data') or '日期' not in v.get('fields',[]):raise ValueError('官方交易日曆缺少日期')
 rows=[]
 for r in v['data']:
  y,mo,d=map(int,r[0].split('/'));day=date(y+1911,mo,d).isoformat()
  if day[:7]!=month[:7]:raise ValueError('日曆月份不符')
  if start<=day<=end:rows.append(day)
 if len(set(rows))!=len(rows):raise ValueError('日曆重複')
 return rows
def extra(kind,market,period,url,v,raw,stamp,cache=None):
 root=m.folder();digest=hashlib.sha256(raw).hexdigest();identity=hashlib.sha256((kind+market+period+digest).encode()).hexdigest();p=root/'raw'/(digest+'.json.gz')
 if not p.exists():p.write_bytes(gzip.compress(raw))
 payload={'raw_path':str(p.relative_to(root)),'cache_observed_at':cache,'original_vintage_known':False,'data':v}
 # Raw body stays compressed; normalized supplementary records only in extras.
 with m.connect(write=True) as c:c.execute('INSERT OR IGNORE INTO extras VALUES(?,?,?,?,?,?,?,?)',(identity,kind,market,period,json.dumps(payload,ensure_ascii=False),url,stamp,digest))
 return identity
def fail(market,kind,day,e):
 with m.connect(write=True) as c:c.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?,?,?)',(market,kind,day,str(e)[:600],m.now()))
def clear(market,kind,day):
 with m.connect(write=True) as c:c.execute('DELETE FROM failures WHERE market=? AND kind=? AND day=?',(market,kind,day))
def fetch_task(task):
 market,kind,day,url=task
 return fetch(url,lambda v:validate(v,market,kind,day))
def run(start=m.START,end='2026-11-04',workers=6,retry=False,post_close=False):
 start=date.fromisoformat(start).isoformat();end=date.fromisoformat(end).isoformat()
 current=datetime.now(m.TZ)
 if start>end or end>current.date().isoformat() or (end==current.date().isoformat() and (not post_close or current.strftime('%H:%M')<'14:00')):raise ValueError('只更新已收盤交易日')
 _BLOCKED.clear()
 m.initialize();root=m.folder();(root/'raw').mkdir(exist_ok=True);lock=(root/'backfill.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise ValueError('已有本頁回補作業，未重複執行')
 with m.connect() as c:history_start=c.execute('SELECT MIN(day) FROM calendar').fetchone()[0]
 state={'status':'running','pid':os.getpid(),'started_at':m.now(),'start':max(m.START,min(start,history_start or start)) if post_close else start,'collection_start':start,'end':end,'completed':0,'failed':0,'total':0,'phase':'calendar','message':'核對官方交易日曆'}
 def progress():state['updated_at']=m.now();state['blocked_sources']=dict(_BLOCKED);m.atomic(root/'job.json',state)
 progress()
 try:
  months=list(month_ranges(start,end))
  for cur,lo,hi in months:
   month=cur.isoformat();url=f'https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={cur:%Y%m%d}&response=json'
   try:
    v,raw,stamp,cache,actual=fetch(url,lambda v:calendar_rows(v,month,start,end),force=post_close);rows=calendar_rows(v,month,start,end);identity=extra('calendar','TWSE',month,actual,{'days':rows},raw,stamp,cache)
    with m.connect(write=True) as c:c.executemany('INSERT OR REPLACE INTO calendar VALUES(?,?)',[(d,identity) for d in rows])
    clear('TWSE','calendar',month);progress()
   except Exception as e:fail('TWSE','calendar',month,e)
  with m.connect() as c:
   days=[r[0] for r in c.execute('SELECT day FROM calendar WHERE day BETWEEN ? AND ? ORDER BY day',(start,end))];existing={(r[0],r[1],r[2]) for r in c.execute('SELECT market,kind,day FROM latest')}
  if not days:raise ValueError('未取得任何官方交易日，無法回補')
  ordered=list(reversed(days));tasks=[(market,kind,day,url) for day in ordered for market,kind,url in urls(day) if (market,kind,day) not in existing]
  if retry:
   with m.connect() as c:retry_days={r[0] for r in c.execute("SELECT DISTINCT day FROM failures WHERE day>=? AND day<=? AND kind IN ('credit','quotes','shares')",(max(m.START,(date.fromisoformat(end)-timedelta(days=45)).isoformat()),end))}
   retry_tasks=[(market,kind,day,url) for day in sorted(retry_days,reverse=True) for market,kind,url in urls(day) if (market,kind,day) not in existing]
   tasks=list(dict.fromkeys(tasks+retry_tasks))
  state.update(phase='daily',total=len(tasks),already_saved=len(existing),calendar_days=len(days),message='回補逐日融資、行情與發行股數');progress()
  iterator=iter(tasks)
  with ThreadPoolExecutor(max_workers=max(1,min(workers,6))) as pool:
   pending={}
   def submit():
    try:t=next(iterator)
    except StopIteration:return
    pending[pool.submit(fetch_task,t)]=t
   for _ in range(workers):submit()
   while pending:
    done,_=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
    for f in done:
     task=pending.pop(f);market,kind,day,url=task
     try:
      v,raw,stamp,cache,actual=f.result();m.save_snapshot(v,market,kind,day,actual,raw,stamp,cache_observed_at=cache);state['completed']+=1
     except Exception as e:fail(market,kind,day,e);state['failed']+=1
     state['current']=market+' '+kind+' '+day;submit()
    if shutil.disk_usage(root).free<2*1024**3:raise RuntimeError('可用空間低於2GB，回補已保留進度並停止')
    progress()
  state.update(phase='events',message='保存除權息、資格調整與分類來源');progress()
  for cur,lo,hi in months:
   for market in ['TWSE','TPEX']:
    a,b=(cur if post_close else lo).isoformat(),hi.isoformat();event_start=date.fromisoformat(a);url=f'https://wwwc.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={event_start:%Y%m%d}&endDate={hi:%Y%m%d}&response=json' if market=='TWSE' else f'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={event_start:%Y/%m/%d}&endDate={hi:%Y/%m/%d}&response=json'
    try:
     v,raw,stamp,cache,actual=fetch(url,lambda v:parse_cash_events(v,market,a,b),force=post_close);events=parse_cash_events(v,market,a,b);extra('corporate_events',market,cur.isoformat(),actual,{'events':events,'coverage':'cash_and_rights_only','split_coverage':'not_verified'},raw,stamp,cache);clear(market,'corporate_events',cur.isoformat())
    except Exception as e:fail(market,'corporate_events',cur.isoformat(),e)
   progress()
  # This endpoint has no trustworthy as-of response date. Save as current observation only.
  url=f'https://www.tpex.org.tw/www/zh-tw/margin/adjust?date={end.replace("-","/")}&response=json'
  try:
   def adjusted(v):
    if str(v.get('stat','')).lower()!='ok' or not v.get('tables'):raise ValueError('資格調整來源未就緒')
   v,raw,stamp,cache,actual=fetch(url,adjusted,True);extra('eligibility_current_observation','TPEX',stamp[:10],actual,{'historical_coverage':False,'tables':v['tables']},raw,stamp,cache)
  except Exception as e:fail('TPEX','eligibility_current_observation',end,e)
  with m.connect() as c:missing=c.execute('SELECT COUNT(*) FROM failures WHERE day BETWEEN ? AND ?',(start[:7]+'-01',end)).fetchone()[0]
  state.update(status='partial' if missing else 'complete',phase='done',finished_at=m.now(),message='來源回補完成；尚有缺漏，請查看品質清單' if missing else '來源回補完成；本金模型尚待包2驗收',unresolved=missing);progress()
 except Exception as e:
  state.update(status='failed',finished_at=m.now(),message=str(e));progress();raise
 finally:lock.close()
 return state
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--start',default=m.START);p.add_argument('--end',default='2026-11-04');p.add_argument('--workers',type=int,default=6);a=p.parse_args();print(json.dumps(run(a.start,a.end,a.workers),ensure_ascii=False))
