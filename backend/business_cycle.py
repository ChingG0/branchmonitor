"""Monthly business-cycle observations. GET is read-only; refresh is finite and explicit."""
from __future__ import annotations
import gzip,hashlib,json,math,os,re,threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from . import db
TZ=ZoneInfo('Asia/Taipei')
NDC='https://index.ndc.gov.tw/n/'
STAT='https://www.stat.gov.tw/Point.aspx?sid={}&n={}&sms=11480'
US_PMI='https://hk.investing.com/economic-calendar/manufacturing-pmi-829'
CATALOG={
 'tw_signal':{'label':'景氣對策信號','region':'台灣','unit':'分','kind':'signal','source':'國家發展委員會','url':NDC+'zh_tw','description':'9項構成項目的綜合分數；紅燈代表景氣熱絡，不是股市賣出訊號。'},
 'tw_pmi':{'label':'製造業 PMI（季調）','region':'台灣','unit':'點','kind':'pmi','source':'國發會／中華經濟研究院','url':NDC+'zh_tw/PMI','description':'50為擴張與收縮分界；與美國S&P Global、ISM屬不同調查。'},
 'tw_cpi':{'label':'CPI 年增率','region':'台灣','unit':'%','kind':'inflation','source':'行政院主計總處','url':STAT.format('t.2','3581'),'description':'同月比上年同月；變化以百分點表示，與季調月增率分開。'},
 'tw_orders':{'label':'外銷訂單年增率','region':'台灣','unit':'%','kind':'orders','source':'經濟部／主計總處統計資訊網','url':STAT.format('t.6','3584'),'description':'由百萬美元原始金額與去年同月計算；外銷訂單不是實際出口額。'},
 'us_pmi':{'label':'S&P Global 製造業 PMI','region':'美國','unit':'點','kind':'pmi','source':'S&P Global','url':US_PMI,'reference_url':US_PMI,'description':'終值與快報分開保存；此處主數列只顯示已核對的終值，不用ISM替代。'},
 'us_cpi':{'label':'CPI 年增率（未季調）','region':'美國','unit':'%','kind':'inflation','source':'美國勞工統計局 BLS','url':'https://www.bls.gov/cpi/','reference_url':'https://hk.investing.com/economic-calendar/cpi-733','description':'CUUR0000SA0總指數的同月年增率；2%線僅供參考，Fed的2%目標是PCE，非CPI。'},
}
_lock=threading.Lock();_state_lock=threading.Lock()
_job={'status':'idle','message':'尚未執行景氣資料更新'}
def folder():return db.DATA/'business_cycle'
def stamp():return datetime.now(TZ).isoformat(timespec='seconds')
def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name('.'+path.name+'.tmp')
 with tmp.open('w') as h:json.dump(value,h,ensure_ascii=False,allow_nan=False);h.flush();os.fsync(h.fileno())
 tmp.replace(path)
def read():
 try:return json.loads((folder()/'latest.json').read_text())
 except FileNotFoundError:return {'series':{},'warnings':[],'checked_at':None}
def job_status():
 with _state_lock:return dict(_job)
def num(value):
 try:v=float(str(value).replace(',','').strip())
 except (ValueError,TypeError):raise ValueError('官方數值不是有限數字')
 if not math.isfinite(v):raise ValueError('官方數值不是有限數字')
 return v
def month(value):
 s=str(value).strip();m=re.fullmatch(r'(\d{4})[-/]?(\d{2})',s) or re.fullmatch(r'(\d{4})/(\d{1,2})/\d{1,2}',s)
 if not m or not 1<=int(m[2])<=12:raise ValueError('無法辨識資料月份')
 return f'{int(m[1]):04d}-{int(m[2]):02d}'
def shift(m,n):
 y,mo=map(int,m.split('-'));i=y*12+mo-1+n
 return f'{i//12:04d}-{i%12+1:02d}'
def normalize(rows,now):
 result={};limit=now[:7]
 for row in rows:
  m=month(row['month']);v=num(row['value'])
  if m>limit:continue
  item={**row,'month':m,'value':v}
  if m in result and result[m]['value']!=v:raise ValueError('同月份有衝突數值，保留舊資料')
  result[m]=item
 if not result:raise ValueError('來源沒有可採用的月資料')
 return [result[m] for m in sorted(result)]
def yearly(rows):
 by={r['month']:r for r in rows};out=[]
 for r in rows:
  old=by.get(shift(r['month'],-12))
  if old and old['value']>0:out.append({**r,'index':r['value'],'value':round((r['value']/old['value']-1)*100,4)})
 if not out:raise ValueError('不足同月去年數據，不能計算年增率')
 return out
def lamp(v):
 if v<9 or v>45:raise ValueError('景氣燈號分數超出9～45')
 return ('藍燈','低迷','#4d83bc') if v<=16 else ('黃藍燈','轉向，注意','#94a6a5') if v<=22 else ('綠燈','穩定','#4e8b71') if v<=31 else ('黃紅燈','轉向，注意','#ca9951') if v<=37 else ('紅燈','熱絡','#c76e62')
def ndc_series(key,value,now):
 if key=='tw_signal':rows=[{'month':p['x'],'value':p['y']} for p in value['line']]
 else:
  title=value['right'][str(value['big'])]
  if 'PMI' not in title['lang']:raise ValueError('來源不是製造業PMI')
  rows=[{'month':p['m'],'value':p['n']} for p in title['d']]
 rows=normalize(rows,now)
 if key=='tw_signal':
  for r in rows:lamp(r['value'])
 elif any(not 0<=r['value']<=100 for r in rows):raise ValueError('PMI超出0～100')
 return {'points':rows,'next_release':value.get('next'),'released_at':None,'release_note':'官方介面未附原始發布時間；取得時間另列。','history_note':'官方首頁回傳近12月，後續更新累積保存。' if key=='tw_signal' else '官方首頁回傳近2月，後續更新累積保存，不補造其他月份。'}
def stat_series(key,raw,now):
 soup=BeautifulSoup(raw,'html.parser');node=soup.find('input',id='ContentPlaceHolder1_hidChartData')
 if not node:raise ValueError('官方統計頁缺少歷史數列')
 values=json.loads(node['value']);title='消費者物價指數年增率' if key=='tw_cpi' else '外銷訂單'
 candidates=[v for v in values if v['Title']==title]
 if key=='tw_cpi':
  candidates=[v for v in candidates if any('月' in str(r.get('Title_2','')) and '預測' not in str(r.get('Title_2','')) for r in v['data'])]
 if len(candidates)!=1:raise ValueError('統計數列缺漏或口徑不明，未選年度預測值')
 rows=normalize([{'month':r['Date'],'value':r['Value']} for r in candidates[0]['data'] if '月' in str(r.get('Title_2','')) and '預測' not in str(r.get('Title_2',''))],now)
 if key=='tw_orders':
  if any(r['value']<=0 for r in rows):raise ValueError('訂單金額應為正數')
  rows=yearly(rows)
  for r in rows:r['amount_million_usd']=r.pop('index')
 return {'points':rows,'released_at':None,'release_note':'官方圖表未附逐筆發布時間；歷史值可能修訂，不能直接當時點回測資料。','next_release':None}
def bls_series(value,now):
 if value.get('status')!='REQUEST_SUCCEEDED':raise ValueError('BLS未成功回傳數列')
 series=value['Results']['series']
 if len(series)!=1 or series[0]['seriesID']!='CUUR0000SA0':raise ValueError('BLS CPI口徑不符')
 values=[r for r in series[0]['data'] if re.fullmatch('M(0[1-9]|1[0-2])',r['period'])]
 missing=[r['year']+'-'+r['period'][1:] for r in values if r['value'] in ('-','.')]
 rows=normalize([{'month':r['year']+'-'+r['period'][1:],'value':r['value']} for r in values if r['value'] not in ('-','.')],now)
 return {'points':[{**r,'value':round(r['value'],1)} for r in yearly(rows)],'released_at':None,'release_note':'BLS API未附逐筆公布時間；年增率由未季調總指數計算。','next_release':None,'missing_index_months':missing,'history_note':'原始總指數缺月：'+('、'.join(sorted(missing)) if missing else '無')+'。無同月去年值的月份不計算年增率，不以鄰月替代。'}
def fetch(url,session=None,post=False):
 s=session or requests.Session();r=s.post(url,timeout=(10,25)) if post else s.get(url,timeout=(10,25))
 r.raise_for_status()
 if len(r.content)>3_000_000:raise ValueError('來源異常過大，停止讀取')
 digest=hashlib.sha256(r.content).hexdigest();p=folder()/'raw'/(digest+'.gz');p.parent.mkdir(parents=True,exist_ok=True)
 if not p.exists():p.write_bytes(gzip.compress(r.content,mtime=0))
 return r.content,{'url':url,'sha256':digest,'retrieved_at':stamp()}
def collect(now):
 results={};errors={}
 try:
  s=requests.Session();raw,meta=fetch(NDC+'zh_tw',s);b=BeautifulSoup(raw,'html.parser');token=b.find('meta',attrs={'name':'csrf-token'})
  if not token:raise ValueError('國發會缺少工作階段驗證')
  s.headers.update({'X-CSRF-TOKEN':token['content'],'Referer':NDC+'zh_tw','X-Requested-With':'XMLHttpRequest'})
  for key,endpoint in [('tw_signal','lightscore'),('tw_pmi','PMI')]:
   try:
    raw,meta=fetch(NDC+'json/'+endpoint,s,True);results[key]={**ndc_series(key,json.loads(raw),now),'evidence':meta}
   except Exception as e:errors[key]=str(e)[:180]
 except Exception as e:
  for key in ('tw_signal','tw_pmi'):errors.setdefault(key,str(e)[:180])
 for key in ('tw_cpi','tw_orders'):
  try:
   raw,meta=fetch(CATALOG[key]['url']);results[key]={**stat_series(key,raw,now),'evidence':meta}
  except Exception as e:errors[key]=str(e)[:180]
 try:
  year=int(now[:4]);url=f'https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0?startyear={year-3}&endyear={year}'
  raw,meta=fetch(url);results['us_cpi']={**bls_series(json.loads(raw),now),'evidence':meta}
 except Exception as e:errors['us_cpi']=str(e)[:180]
 # S&P's public historical data is not an open API. Verified finals are imported
 # separately; never infer final/flash status from an Investing release date.
 errors['us_pmi']='S&P Global公開頁面目前限制自動讀取；保留對話查核終值，新發布請在對話中手動補齊。'
 return results,errors

def summarize(key,series):
 points=series.get('points',[])
 if not points:return {'status':'missing','reading':'尚無資料','color':'#8f9b91','change':None}
 r=points[-1];v=r['value'];prev=next((p for p in reversed(points[:-1]) if p['month']==shift(r['month'],-1)),None)
 delta=round(v-prev['value'],4) if prev else None
 if key=='tw_signal':label,reading,color=lamp(v);reading=label+' · '+reading
 elif CATALOG[key]['kind']=='pmi':
  reading=('擴張' if v>50 else '收縮' if v<50 else '持平')+('加快' if delta is not None and v>50 and delta>0 else '放慢' if delta is not None and v>50 and delta<0 else '改善' if delta is not None and v<50 and delta>0 else '加深' if delta is not None and v<50 and delta<0 else '')
  color='#4d8a70' if v>=50 else '#c18b48'
 elif key=='tw_orders':reading='訂單年增' if v>=0 else '訂單年減';color='#4d8a70' if v>=0 else '#c18b48'
 else:reading='通膨升溫' if delta is not None and delta>0 else '通膨降溫' if delta is not None and delta<0 else '通膨持平';color='#c18b48' if delta is not None and delta>0 else '#628b9f'
 return {'status':'available','reading':reading,'color':color,'change':delta,'previous_month':prev['month'] if prev else None}
def refresh_sync(*,collector=None,now=None):
 now=now or stamp();old=read();series=old.get('series',{});results,errors=(collector or collect)(now)
 for key,new in results.items():
  try:
   points=normalize(new['points'],now);existing={p['month']:p for p in series.get(key,{}).get('points',[])}
   existing.update({p['month']:p for p in points})
   series[key]={**CATALOG[key],**new,'key':key,'points':[existing[m] for m in sorted(existing)],'checked_at':now,'error':None}
  except (ValueError,KeyError,TypeError) as e:errors[key]=str(e)
 manual=folder()/'manual'/'us_pmi.json'
 if manual.exists():
  try:
   saved=json.loads(manual.read_text());points=normalize(saved['points'],now)
   if saved['verified_at']>now or saved.get('released_at','')>now:raise ValueError('人工查核或發布時間在未來')
   # The actual primary-source link and real check time accompany every import.
   if not saved.get('verified_at') or not saved.get('source_url','').startswith('https://www.spglobal.com/'):raise ValueError('人工終值缺少來源查核證據')
   existing={p['month']:p for p in series.get('us_pmi',{}).get('points',[])};existing.update({p['month']:p for p in points})
   series['us_pmi']={**CATALOG['us_pmi'],**saved,'key':'us_pmi','points':[existing[m] for m in sorted(existing)],'checked_at':saved['verified_at'],'released_at':saved.get('released_at'),'url':saved['source_url'],'manual':True}
  except (ValueError,KeyError,TypeError) as e:errors['us_pmi']=str(e)
 for key in CATALOG:
  if key in series:series[key]['error']=errors.get(key)
  if key not in series:errors.setdefault(key,'尚無資料，請手動更新')
 result={'version':'business-cycle.v1','checked_at':now,'series':series,'warnings':[{'key':k,'message':v} for k,v in errors.items()]}
 atomic(folder()/'latest.json',result)
 return {'status':'partial' if errors else 'complete','message':f'已保存 {len(series)}/{len(CATALOG)} 項景氣月資料；'+('部分來源需手動補齊，不自動重試' if errors else '來源已核對'),'checked_at':now}
def refresh():
 if not _lock.acquire(blocking=False):return job_status()
 with _state_lock:_job.update(status='running',message='更新官方景氣月資料，每個來源只嘗試一次')
 def worker():
  try:
   value=refresh_sync()
   with _state_lock:_job.update(value)
  except Exception as e:
   with _state_lock:_job.update(status='failed',message='保留既有資料；'+str(e)[:180])
  finally:_lock.release()
 try:threading.Thread(target=worker,daemon=True).start()
 except Exception:
  _lock.release();raise
 return job_status()
def report():
 saved=read();items=[]
 for key,spec in CATALOG.items():
  s={**spec,'key':key,**saved.get('series',{}).get(key,{'points':[]})};s['summary']=summarize(key,s);items.append(s)
 available=sum(bool(s['points']) for s in items)
 return {'checked_at':saved.get('checked_at'),'series':items,'warnings':saved.get('warnings',[]),'job':job_status(),'coverage':{'available':available,'total':len(CATALOG)},'note':'月資料反映已公布景氣，不等於股市漲跌機率。發布日、資料月、取得時間不同；目前歷史為最新修訂版，未自動納入V6或跟單。'}
def home_status():
 r=report();missing=bool(r['warnings']) or r['coverage']['available']<len(CATALOG)
 return {'name':'景氣循環分析','status':'partial' if missing and r['coverage']['available'] else 'queued' if not r['coverage']['available'] else 'complete','message':f"已保存 {r['coverage']['available']}/{len(CATALOG)} 項月資料 · 最近取得 {r['checked_at'][:10] if r['checked_at'] else '尚未更新'}"+('；來源未齊，請手動補齊' if missing else '')}
if __name__=='__main__':print(json.dumps(refresh_sync(),ensure_ascii=False))
