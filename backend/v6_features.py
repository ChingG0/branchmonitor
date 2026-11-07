"""Independent, retrospective V6 macro/positioning research; no production writes."""
import json,re,math,hashlib
from pathlib import Path
import numpy as np,pandas as pd
from datetime import datetime,timedelta
ROOT=Path(__file__).resolve().parents[1] / 'research' / 'v6_macro_20261017'
RAW=ROOT/'raw'
from . import v6_engine as E

def jread(p):return json.loads(p.read_text())
def clean(x):
 if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
 if isinstance(x,(list,tuple)):return [clean(v) for v in x]
 if isinstance(x,np.generic):return clean(x.item())
 if isinstance(x,float) and not math.isfinite(x):return None
 return x

def yahoo(name,taiwan=False):
 r=jread(RAW/(name+'.json'))['chart']['result'][0]
 dt=pd.to_datetime(r['timestamp'],unit='s',utc=True).tz_convert(r['meta'].get('exchangeTimezoneName','America/New_York'))
 q=r['indicators']['quote'][0];v=r['indicators'].get('adjclose',[{}])[0].get('adjclose',q['close'])
 z=pd.DataFrame({'date':dt.strftime('%Y-%m-%d'),'value':v}).dropna().drop_duplicates('date').set_index('date').sort_index()
 # US/global daily close unavailable until following Taiwan morning; FX conservative full UTC day.
 z['available']=pd.to_datetime(z.index).tz_localize('Asia/Taipei')+(pd.Timedelta(hours=16) if taiwan else pd.Timedelta(days=1,hours=8))
 z['r1']=z.value.pct_change();z['r5']=z.value.pct_change(5);z['change5']=z.value.diff(5)
 return z

def align(z,decision,prefix):
 left=pd.DataFrame({'decision':decision,'row':np.arange(len(decision))})
 right=z.reset_index(drop=True).sort_values('available')
 m=pd.merge_asof(left.sort_values('decision'),right,left_on='decision',right_on='available',direction='backward',tolerance=pd.Timedelta(days=7)).sort_values('row')
 assert ((m.available<=m.decision)|m.available.isna()).all()
 return {prefix+'_'+k:m[k].to_numpy() for k in z.columns}

def positions(code):
 a=pd.concat([pd.read_csv(p,encoding='cp950',index_col=False) for p in sorted(RAW.glob('positions-'+code+'-*.csv'))],ignore_index=True)
 a['date']=pd.to_datetime(a.iloc[:,0]).dt.strftime('%Y-%m-%d')
 return a

def build(raw):
 f=E.features(raw['index']);dates=pd.to_datetime(f.index)
 nxt=list(dates[1:])+[dates[-1]+pd.offsets.BDay(1)]
 decision=pd.DatetimeIndex(nxt).tz_localize('Asia/Taipei')+pd.Timedelta(hours=8,minutes=30)
 f['decision_at']=[v.isoformat() for v in decision]
 for name,tw in [('twd',False),('oil',False),('spx',False),('tsm',False),('rates-proxy',False),('financial',True),('electronics',True)]:
  z=yahoo(name,tw)
  for k,v in align(z,decision,name).items():f[k]=v
 z=pd.read_csv(RAW/'vix.csv');z['available']=pd.to_datetime(z.DATE).dt.tz_localize('Asia/Taipei')+pd.Timedelta(days=1,hours=8);z['value']=z.CLOSE;z['r1']=z.value.pct_change();z['r5']=z.value.pct_change(5)
 for k,v in align(z[['available','value','r1','r5']],decision,'vix').items():f[k]=v
 z=pd.DataFrame(jread(RAW/'cnn-2021.json')['fear_and_greed_historical']['data']);z['date']=pd.to_datetime(z.x,unit='ms',utc=True).dt.strftime('%Y-%m-%d')
 z=z[z.date>='2021-01-22'].drop_duplicates('date',keep='last').sort_values('date') # initial constant-50 block excluded
 z['available']=pd.to_datetime(z.date).dt.tz_localize('Asia/Taipei')+pd.Timedelta(days=1,hours=8);z['value']=z.y;z['change5']=z.y.diff(5)
 for k,v in align(z[['available','value','change5']],decision,'feargreed').items():f[k]=v
 tx=positions('TXF');tx=tx[tx.iloc[:,2].str.contains('外資')].set_index('date').sort_index()
 long=tx.iloc[:,9].astype(float);short=tx.iloc[:,11].astype(float)
 z=pd.DataFrame({'value':(short-long)/(short+long),'net_short':short-long});z['change5']=z.net_short.diff(5);z['available']=pd.to_datetime(z.index).tz_localize('Asia/Taipei')+pd.Timedelta(hours=20)
 for k,v in align(z,decision,'foreign').items():f[k]=v
 mx=positions('MXF');inst=mx.groupby('date')[[mx.columns[9],mx.columns[11]]].sum();oi=[]
 for p in sorted(RAW.glob('mtx-month-*.csv')):
  try:
   t=pd.read_csv(p,encoding='cp950',index_col=False);t.columns=t.columns.str.strip();t=t.apply(lambda s:s.str.strip() if s.dtype=='object' else s)
   t=t[t['交易時段']=='一般'];t=t[t['到期月份(週別)'].astype(str).str.fullmatch(r'\d{6}(W\d)?')]
   t['未沖銷契約數']=pd.to_numeric(t['未沖銷契約數'],errors='coerce');oi.append(t.groupby('交易日期')['未沖銷契約數'].sum())
  except Exception as e:print('OI parse',p.name,str(e)[:100])
 if oi:
  total=pd.concat(oi);total.index=pd.to_datetime(total.index).strftime('%Y-%m-%d');total=total[~total.index.duplicated()].sort_index()
  z=pd.DataFrame({'value':(inst.iloc[:,1]-inst.iloc[:,0])/total*100}).dropna();z['change5']=z.value.diff(5);z['available']=pd.to_datetime(z.index).tz_localize('Asia/Taipei')+pd.Timedelta(hours=20)
  for k,v in align(z,decision,'retail').items():f[k]=v
 else:f['retail_value']=np.nan;f['retail_change5']=np.nan
 delta=f.close.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean();f['rsi']=100-100/(1+up/down);f['rsi_change3']=f.rsi.diff(3)
 # Monthly weakness uses only complete preceding calendar years, updated once each Jan.
 mon=f.close.copy();mon.index=pd.to_datetime(mon.index);mon=mon.resample('M').last().pct_change();seas=[]
 for d in dates:
  history=mon[mon.index.year<d.year];means=history.groupby(history.index.month).mean();weak=set(means.nsmallest(3).index) if len(history)>=36 else set();seas.append(d.month in weak)
 f['weak_month']=seas
 cpi=[datetime.strptime(re.search(r'cpi_(\d{8})',s).group(1),'%m%d%Y') for s in jread(ROOT/'cpi-urls.json')]
 cpi=pd.DatetimeIndex(cpi).tz_localize('America/New_York')+pd.Timedelta(hours=8,minutes=30)
 tsm=[]
 for p in [ROOT/'tsmc-web.txt',ROOT/'tsmc-web2.txt']:
  date=None
  for line in p.read_text().splitlines():
   match=re.search(r'([A-Z][a-z]+ \d{1,2}, \d{4}) \(',line)
   if match:date=datetime.strptime(match.group(1),'%B %d, %Y')
   if 'Results - Earnings Conference' in line and date:tsm.append(date)
 tsm=pd.DatetimeIndex(sorted(set(tsm))).tz_localize('Asia/Taipei')+pd.Timedelta(hours=20)
 for name,events in [('cpi',cpi),('tsmc',tsm)]:
  f[name+'_recent']=[any(pd.Timedelta(0)<=d-e<=pd.Timedelta(days=3) for e in events) for d in decision]
 return f
