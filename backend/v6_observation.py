"""Unfitted conditional observation, explicitly NOT a calibrated night forecast.

Fixed rules, no coefficient fitting or accuracy claims. Today's partial-night
TX return is a proxy for the next cash opening gap, not the05:00 close. Historical
cash gaps are proxies, not fabricated historical18/22 night quotes. M0 remains
an audit comparator only. All inputs/outcomes are restricted to the cutoff.
"""
from __future__ import annotations
import hashlib,json,math
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import numpy as np
from . import db,v6_evening_model as M
TZ=ZoneInfo('Asia/Taipei')
METHOD='v6-observation-1.0.0'
RULES={'method':METHOD,'trained':False,'calibrated':False,'historySessions':5040,
 'cashGapProxy':True,'sameMonthWeight':1.5,'seasonalDriftWeight':.10,
 'uncertaintyMass':.25,'stressGridPoints':201,'uncertaintyLogRadius':'max(4*sigma*sqrt(h),2*abs(nightLogReturn))',
 'analogYears':5,'annualMatching':'completed monthly returns + same session-count MTD and YTD; outcomes excluded from distance',
 'promotion':'observation only; never infer qualified-model adoption or reset old formal samples'}
def sha(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
def as_time(x):
 d=datetime.fromisoformat(x) if isinstance(x,str) else x
 if d.tzinfo is None:raise ValueError('來源時間缺時區')
 return d.astimezone(TZ)
def quantile(values,weights,qs):
 order=np.argsort(values);x=np.asarray(values)[order];w=np.asarray(weights)[order];c=np.cumsum(w)/np.sum(w)
 return np.array([x[min(len(x)-1,int(np.searchsorted(c,q,side='left')))] for q in qs])
def valid_quote(row,cutoff,anchor,night=False):
 try:
  stamp=as_time(row['quote_at']);cut=as_time(cutoff)
  if not row.get('included') or stamp>cut:return False
  if night:
   begin=as_time(anchor+'T15:00:00+08:00');end=begin+timedelta(hours=14)
   if row.get('day_close_date')!=anchor or not begin<=stamp<=end:return False
   if not (float(row['price'])>0 and float(row['day_close'])>0):return False
   return (cut-stamp).total_seconds()<=15*60 or (cut>=end and row.get('state')=='closed' and stamp>=end-timedelta(minutes=30))
  return stamp>as_time(anchor+'T13:30:00+08:00') and ((cut-stamp).total_seconds()<=20*60 or row.get('state')=='closed')
 except (KeyError,TypeError,ValueError,OverflowError):return False

def monthly_context(prices,monthly,anchor):
 """Never read a current-month final value, future years or all-years UI stats."""
 year=int(anchor[:4]);month=int(anchor[5:7]);by={}
 for r in monthly:
  if r['date']<anchor[:7]+'-01' and r.get('complete') is True:by[r['month']]=float(r['close'])
 # Official completed daily history can repair recent absent monthly closes.
 for d,row in prices.iterrows():
  if str(d)<anchor[:7]+'-01':by[str(d)[:7]]=float(row.close)
 returns={}
 for k,v in sorted(by.items()):
  dt=datetime.fromisoformat(k+'-01');prior=(dt-timedelta(days=1)).strftime('%Y-%m')
  if prior in by:returns[k]=v/by[prior]-1
 prior_month=(datetime.fromisoformat(anchor[:7]+'-01')-timedelta(days=1)).strftime('%Y-%m')
 close=float(prices.loc[anchor,'close']);mtd=close/by[prior_month]-1 if prior_month in by else None
 annual_base=by.get(f'{year-1}-12');ytd=close/annual_base-1 if annual_base else None
 def stats(vals):
  vals=np.array(vals,float)
  return {'n':len(vals),'upRate':float(np.mean(vals>0)) if len(vals) else None,
   'meanPct':float(np.mean(vals)*100) if len(vals) else None,'minPct':float(np.min(vals)*100) if len(vals) else None,
   'maxPct':float(np.max(vals)*100) if len(vals) else None,'stdPct':float(np.std(vals,ddof=1)*100) if len(vals)>1 else None}
 vals=[v for k,v in returns.items() if int(k[:4])<year and int(k[5:])==month]
 monthly_stats=stats(vals);annual=[]
 for y in range(min([int(k[:4]) for k in by],default=year)+1,year):
  if all(f'{y}-{m:02}' in returns for m in range(1,13)) and f'{y-1}-12' in by and f'{y}-12' in by:annual.append(by[f'{y}-12']/by[f'{y-1}-12']-1)
 current=prices.loc[[d for d in prices.index if str(d).startswith(anchor[:7])]]
 return {'month':month,'asOf':anchor,'currentMonthPct':None if mtd is None else mtd*100,
  'currentYtdPct':None if ytd is None else ytd*100,'month':month,
  'monthStats':monthly_stats,'annualStats':stats(annual),
  'monthDeviationPp':None if mtd is None or not vals else mtd*100-monthly_stats['meanPct'],
  'monthPercentile':None if mtd is None or not vals else float(np.mean(np.array(vals)<=mtd)*100),
  'comparisonNote':'月迄今與歷史完整月份只作位置比較，不能當同進度勝率；極值是月底對上月底報酬，不是月內最高／最低點。',
  'monthlyHistoryFirst':min(returns) if returns else None,'lastCompletedMonth':max(by) if by else None,
  'currentMonthSessionCount':len(current),'completedReturns':returns,'monthCloses':by}

def year_analogs(prices,context,anchor):
 year=int(anchor[:4]);month=int(anchor[5:7]);n=context['currentMonthSessionCount'];rets=context['completedReturns'];base=context['monthCloses'];candidates=[]
 # Current partial month never gets compared with another year's final month.
 def prefix(y):
  ds=[str(d) for d in prices.index if str(d).startswith(f'{y}-{month:02}')]
  if len(ds)<n or n<1 or f'{y-1}-12' not in base:return None
  d=ds[n-1];idx=prices.index.get_loc(d)
  pm=(datetime(y,month,1)-timedelta(days=1)).strftime('%Y-%m')
  if pm not in base:return None
  completed=[rets.get(f'{y}-{m:02}') for m in range(1,month)]
  if any(v is None for v in completed):return None
  close=float(prices.loc[d,'close']);return d,idx,[*completed,close/base[pm]-1,close/base[f'{y-1}-12']-1]
 cur=prefix(year)
 if cur is None:return {'cases':[],'reason':'同進度月份或年初基準不足','matchingUsesFutureOutcomes':False}
 for y in sorted({int(str(d)[:4]) for d in prices.index if int(str(d)[:4])<year}):
  p=prefix(y)
  if p and p[1]+60<len(prices) and str(prices.index[p[1]+60])<=anchor:candidates.append((y,*p))
 if not candidates:return {'cases':[],'reason':'缺同進度且後60日已實現的年份','matchingUsesFutureOutcomes':False}
 vectors=np.asarray([x[3] for x in candidates]);current=np.asarray(cur[2]);scale=np.maximum(np.std(vectors,axis=0),.02)
 distances=np.sqrt(np.mean(((vectors-current)/scale)**2,axis=1));order=np.argsort(distances)[:5];cases=[]
 for rank,k in enumerate(order,1):
  y,d,i,v=candidates[k];start=float(prices.close.iloc[i]);path=[{'day':s,'pct':(float(prices.close.iloc[i+s])/start-1)*100} for s in range(61)]
  cases.append({'year':y,'rank':rank,'distance':float(distances[k]),'matchedThrough':d,
   'mtdPct':v[-2]*100,'ytdPct':v[-1]*100,'outcomes':[{'sessions':s,'date':str(prices.index[i+s]),'returnPct':path[s]['pct']} for s in (1,3,5,20,60)],'path':path})
 return {'cases':cases,'sessionCount':n,'matchingUsesFutureOutcomes':False,
  'description':'依已完成各月報酬＋同月相同交易日數的月迄今／年迄今報酬排序；後續走勢不參與挑選。距離越小越像，不是重演機率。',
  'outcomeNote':'只展示歷史案例；後20／60日不能視為本次T+1～T+3預測。'}

class Observation:
 def __init__(self,prices,live,cutoff,monthly):
  self.cutoff=as_time(cutoff);self.prices=prices.loc[[str(d)<=self.cutoff.date().isoformat() for d in prices.index]].copy()
  self.anchor=str(self.prices.index[-1]);self.close=self.prices.close.to_numpy(float);self.log=np.log(self.close);self.sigma=M.sigma_series(self.close)
  self.context=monthly_context(self.prices,monthly,self.anchor);self.analogs=year_analogs(self.prices,self.context,self.anchor)
  self.night=next(iter(sorted([r for r in (live or {}).get('night',[]) if valid_quote(r,cutoff,self.anchor,True)],key=lambda r:r['contract'])),None)
  self.overseas=[r for r in (live or {}).get('overseas',[]) if valid_quote(r,cutoff,self.anchor)]
  self.gap=math.log(self.night['price']/self.night['day_close']) if self.night else None
  self.current_sigma=float(self.sigma[-1]);signs=[np.sign(float(r['change_pct'])) for r in self.overseas if r['key'] in ('spx','sox','tsm')]
  # Overseas overlap with TX is not counted as a second independent return.
  self.disagreement=float(np.mean([s!=np.sign(self.gap) for s in signs])) if self.gap is not None and signs else 0.
  self.sigma_used=self.current_sigma*(1+.25*self.disagreement)
  if self.night:self.sigma_used=max(self.sigma_used,float(self.night.get('session_range_pct',0))/100/2)
  self.inputs={'night':self.night,'overseas':self.overseas,'nightLogReturn':self.gap,'sigmaPct':self.sigma_used*100,
   'overseasDisagreement':self.disagreement,'nightMissing':self.night is None,
   'nightNote':'當下夜盤相對同合約日盤是隔日現貨跳空的代理；不是已知隔日開盤，也非05:00收盤。',
   'overseasNote':'美股／ADR只檢查與夜盤分歧並放寬不確定性，不疊加成額外報酬。',
   'rules':RULES,'rulesSha256':sha(RULES)}
 def predict(self,h):
  i=len(self.close)-1;j=np.arange(60,i-h+1,dtype=int)[-RULES['historySessions']:]
  if len(j)<252:raise ValueError('觀察版歷史參考不足252筆，不編造機率')
  logs=self.log;sig=self.sigma;root=math.sqrt(20)
  dd=lambda k:(logs[k]-max(logs[max(0,k-59):k+1]))/(sig[k]*root)
  ret=lambda k:(logs[k]-logs[k-20])/(sig[k]*root)
  distance=np.array([((dd(k)-dd(i))/2)**2+((ret(k)-ret(i))/2)**2+(math.log(sig[k]/sig[i]))**2 for k in j])
  weights=np.exp(-.5*np.minimum(distance,100))
  # Same calendar month is a fixed modest weight, not a 60-year frequency used as daily P.
  weights*=np.array([RULES['sameMonthWeight'] if str(self.prices.index[k])[5:7]==self.anchor[5:7] else 1 for k in j])
  if self.gap is not None:
   openings=self.prices.open.to_numpy(float);gap=np.log(openings[j+1]/self.close[j]);scaled_gap=gap/sig[j]
   weights*=np.exp(-.5*np.minimum(((scaled_gap-self.gap/sig[i])/2)**2,100))
   remaining=logs[j+h]-np.log(openings[j+1]);returns=self.gap+remaining/sig[j]*self.sigma_used
  else:returns=(logs[j+h]-logs[j])/sig[j]*self.sigma_used
  mean=self.context['monthStats']['meanPct'];seasonal=RULES['seasonalDriftWeight']*math.log1p(mean/100)/21*h if mean is not None else 0.
  returns=returns+seasonal
  if not np.isfinite(weights).all() or float(weights.sum())<=0:raise ValueError('觀察參考權重無效')
  effective=float(weights.sum()**2/(weights@weights));weights=weights/weights.sum()*(1-RULES['uncertaintyMass'])
  # Explicit stress mass prevents unvalidated intranight certainty. Not a calibrated coverage claim.
  radius=max(4*self.sigma_used*math.sqrt(h),2*abs(self.gap or 0.))
  grid=np.linspace(-radius,radius,RULES['stressGridPoints']);returns=np.r_[returns,grid];weights=np.r_[weights,np.full(len(grid),RULES['uncertaintyMass']/len(grid))]
  p=float(weights[returns>1e-14].sum()+.5*weights[np.abs(returns)<=1e-14].sum());q=quantile(returns,weights,[.1,.5,.9])
  if abs(p-.5)<1e-12:p=.5;q[1]=0.
  anchor=float(self.close[-1]);levels=anchor*np.exp(q)
  if not M.coherence(p,anchor,levels):raise ValueError('觀察機率／點數不一致')
  return {'horizon':h,'up':p,'down':1-p,'direction':M.direction(p),'anchorClose':anchor,
   'medianClose':float(levels[1]),'medianChangePoints':float(levels[1]-anchor),'q10Close':float(levels[0]),'q90Close':float(levels[2]),
   'q10ChangePoints':float(levels[0]-anchor),'q90ChangePoints':float(levels[2]-anchor),
   'model':'OBS','methodVersion':METHOD,'modelLabel':'未校準觀察版','calibrated':False,'trained':False,'coherent':True,
   'sampleN':len(j),'effectiveSampleN':effective,'dailySigmaPct':self.sigma_used*100,'seasonalDriftLog':seasonal,
   'featuresUsed':['現貨回撤／20日強弱','近期波動','同月份歷史權重','歷史完整月份平均的固定小幅偏移']+(['當下同合約夜盤變動（現貨跳空代理）'] if self.night else []),
   'featuresNotUsed':['05:00完整夜盤替代分時','未取得公布時間的其他分頁','相似年份的後續走勢'],
   'lastTrainingOutcome':str(self.prices.index[j[-1]+h]),'modelSelectionReason':'依使用者要求直接觀察固定規則的即時變化，未訓練、未校準、未通過1%Brier採用門檻',
   'intervalNote':'第10／90百分位含25%對稱壓力情境；是保守觀察範圍，尚未驗證80%實際涵蓋。'}
 def public_context(self):
  c={k:v for k,v in self.context.items() if k not in ('completedReturns','monthCloses')}
  return {'methodVersion':METHOD,'rules':RULES,'rulesSha256':sha(RULES),'inputs':self.inputs,'seasonality':c,'yearAnalogs':self.analogs,
   'status':'untrained_uncalibrated_observation','dataNote':'主畫面為未校準觀察版；M0只保留在研究比較。歷史日線代理不能冒充18/22分時訓練。'}

def load_monthly():
 from . import monthly_statistics as S
 raw=S._read();return raw['markets']['taiex']['closes'],{'savedAt':raw.get('saved_at'),'sha256':sha(raw['markets']['taiex']['closes'])}
