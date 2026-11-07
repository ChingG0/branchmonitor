"""Chat-only stock exposure limits. Probabilities are never a portfolio weight.
Read paths project saved forecasts; only a publish call creates a new decision.
"""
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo
import hashlib, json, math
import numpy as np
from . import db

CONFIG=db.ROOT/'config/v6_allocation/policy.json'
TZ=ZoneInfo('Asia/Taipei')

def policy():return json.loads(CONFIG.read_text())
def fingerprint():return hashlib.sha256(CONFIG.read_bytes()).hexdigest()

def features(prices):
 f=prices.copy();close=f.close;returns=np.log(close/close.shift(1))
 f['ma20']=close.rolling(20).mean();f['ma60']=close.rolling(60).mean()
 f['ma5']=close.rolling(5).mean();f['ma5Change']=f.ma5-f.ma5.shift(1)
 tr=np.maximum(f.high-f.low,np.maximum(abs(f.high-close.shift(1)),abs(f.low-close.shift(1))))
 f['atr20']=tr.rolling(20).mean();f['stretchAtr']=np.where(f.atr20>0,(close-f.ma20)/f.atr20,0.)
 f['annualVolPct']=np.maximum(returns.rolling(20).std(),returns.ewm(alpha=.06,adjust=False).std())*math.sqrt(252)*100
 f['drawdownPct']=(close/close.rolling(252,min_periods=60).max()-1)*100
 f['break20']=close<close.shift(1).rolling(20).min()
 return f

def decision(row,*,previous=None,targets=None,rules=None):
 p=rules or policy();anchor=str(row.name)
 needed=('close','ma20','ma60','atr20','annualVolPct','drawdownPct','stretchAtr','ma5Change')
 if not all(math.isfinite(float(row[k])) for k in needed):raise ValueError('至少需要60筆完整行情才能計算股票配置')
 caps=[{'key':'base','label':'股票曝險上限','pct':float(p['maxEquityPct'])}]
 def cap(key,label,value):caps.append({'key':key,'label':label,'pct':max(0.,min(p['maxEquityPct'],float(value)))})
 cap('volatility','波動風險預算',p['targetAnnualVolPct']/max(float(row.annualVolPct),.01)*100)
 for threshold,limit in p['overheatAtrCaps']:
  if row.stretchAtr>=threshold:cap('overheat',f'偏離20MA達{threshold}倍ATR，限制追高',limit)
 for threshold,limit in p['drawdownCaps']:
  if row.drawdownPct<=threshold:cap('drawdown',f'距近一年高點回撤達{abs(threshold)}%',limit)
 if row.close<row.ma20 and row.ma5Change<0:cap('trend20','低於20MA且5MA下降',50)
 if row.close<row.ma60:cap('trend60','低於60MA',35)
 if bool(row.break20):cap('break20','跌破前20日收盤低點',25)
 probability_used=False
 if targets:
  if len(targets)!=3 or any(t['anchorClose']!=float(row.close) or t['targetDate']<=anchor for t in targets):raise ValueError('配置與機率必須使用同一現貨錨點及未來到期日')
  probability_used=True;down=max(float(t['down']) for t in targets)
  for threshold,limit in p['downProbabilityCaps']:
   if down>=threshold:cap('probability',f'某期限下跌觀察值達{threshold:.0%}，只作減碼限制',limit)
  tail=max(max(0.,1-float(t['q10Close'])/row.close) for t in targets)
  if tail>0:cap('tail','三期限最不利q10情境的股票風險預算',p['tailBudgetPct']/tail)
 raw=min(c['pct'] for c in caps);prior=float(previous['equityPct']) if previous else None
 if previous and previous['anchorDate']>anchor:raise ValueError('不可使用未來配置')
 # Reductions bypass smoothing. A same-day revision can reduce again, but
 # never restore risk already cut; refreshes cannot manufacture add capacity.
 add_ceiling=(prior if previous['anchorDate']==anchor else prior+p['maxDailyAddPp']) if previous else p['initialEquityPct']
 target=round(min(raw,add_ceiling),2)
 binding=[c for c in caps if abs(c['pct']-raw)<1e-8]
 if target<raw-.005:binding.append({'key':'slow_add','label':'加碼每交易日最多5個百分點；同日不重複加碼','pct':target})
 delta=None if prior is None else round(target-prior,2)
 return {'version':p['version'],'anchorDate':anchor,'anchorClose':float(row.close),'equityPct':target,'cashPct':round(100-target,2),'rawEquityPct':round(raw,2),'previousEquityPct':prior,'changePp':delta,'action':'建立上限' if prior is None else '降低股票曝險' if delta<0 else '緩步提高上限' if delta>0 else '維持上限','limits':caps,'binding':binding,'inputs':{k:float(row[k]) for k in needed},'probabilityUsed':probability_used,'policySha256':fingerprint(),'adoptionStatus':'observation_only_not_validated','note':'固定規則回測尚未證明優於舊版，僅作觀察上限，非已驗證操作策略或實際持倉；未校準機率僅能壓低上限，不能因看漲而放大配置。現金不代表避開所有損失。'}

def calculate(prices,targets,previous=None):
 f=features(prices)
 return decision(f.iloc[-1],previous=previous,targets=targets)

def _simulate(prices,weights,start,end,rules):
 frame=prices.loc[start:end];units=0.;cash=1.;peak=1.;mdd=0.;turnover=0.;curve=[]
 dates=list(prices.index);positions={d:i for i,d in enumerate(dates)}
 for day,row in frame.iterrows():
  i=positions[day];signal_day=dates[i-1] if i else None
  weight=float(weights.get(signal_day,0.));weight=max(0.,min(1.,weight))
  value=cash+units*row.open;stock=units*row.open;gap=weight*value-stock
  cost=rules['buyCostBps' if gap>=0 else 'sellCostBps']/10000
  traded=gap/(1+weight*cost) if gap>=0 else gap/(1-weight*cost)
  cash-=traded+abs(traded)*cost;units+=traded/row.open;turnover+=abs(traded)/value
  value=cash+units*row.close;peak=max(peak,value);mdd=min(mdd,value/peak-1)
  curve.append({'date':str(day),'equity':round(value,8),'drawdownPct':round((value/peak-1)*100,4),'stockPct':round(units*row.close/value*100,2)})
 years=len(frame)/252
 return {'start':str(frame.index[0]),'end':str(frame.index[-1]),'sessions':len(frame),'returnPct':(value-1)*100,'cagrPct':(value**(1/years)-1)*100,'maxDrawdownPct':mdd*100,'turnover':turnover,'curve':curve}

def study(prices,archived=()):
 """Causal diagnostic, fixed rules, no historical forecast probabilities added.
 Each close's weight first executes at the following day's open with costs.
 """
 p=policy();f=features(prices);previous=None;decisions=[];weights={}
 for day,row in f.iterrows():
  try:d=decision(row,previous=previous,rules=p)
  except ValueError:continue
  previous=d;decisions.append(d);weights[str(day)]=d['equityPct']/100
 if not decisions:raise ValueError('配置診斷歷史不足')
 ready=decisions[0]['anchorDate'];start=max('2000-01-01',ready);end=str(prices.index[-1])
 fixed={str(d):.6 for d in prices.index}
 periods={}
 for label,lo,hi in [('all',start,end),('2020','2020-01-01','2020-12-31'),('2022','2022-01-01','2022-12-31'),('recent','2026-10-01',end)]:
  if prices.loc[lo:hi].empty:continue
  periods[label]={'protective':_simulate(prices,weights,lo,hi,p),'fixed60':_simulate(prices,fixed,lo,hi,p)}
 # Original seven-factor allocations are not reconstructed with today's factors.
 # Only decisions actually saved before a day's 09:00 are eligible for its open.
 if archived and 'recent' in periods:
  ordered=sorted(archived,key=lambda x:x['savedAt']);old_weights={};j=0;weight=0.
  dates=list(prices.index)
  for i in range(1,len(dates)):
   opening=datetime.fromisoformat(str(dates[i])).replace(hour=9,tzinfo=TZ)
   while j<len(ordered) and datetime.fromisoformat(ordered[j]['savedAt'])<opening:
    weight=ordered[j]['equityPct']/100;j+=1
   old_weights[str(dates[i-1])]=weight
  periods['recent']['archived']=_simulate(prices,old_weights,'2026-10-01',end,p)
 for block in periods.values():
  for result in block.values():result['curve']=result['curve'][-80:]
 return {'version':p['version'],'createdAt':datetime.now(TZ).isoformat(),'policy':p,'policySha256':fingerprint(),'codeSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'pricesSha256':hashlib.sha256(prices.to_csv(float_format='%.17g').encode()).hexdigest(),'latestPreview':decisions[-1],'recentDecisions':[d for d in decisions if d['anchorDate']>='2026-09-01'],'periods':periods,'forecastOverlayEvaluated':False,'status':'historical_diagnostic_not_unseen_test','note':'參數先固定，未按結果調整。資料已曾用於研究，非全新盲測；僅比較價格防護層，不含歷史夜盤機率。加權指數為無股息、無現金利息的代理；次日09:00開盤再平衡，含買入19.25bp及賣出49.25bp成本。舊版只用真實保存時間以前的配置；新舊更新頻率不同。回撤是每日收盤估值，未計盤中最深回撤，不代表可完全複製的績效。'}


def read_study():
    """GET only reads a saved diagnostic. Never calculates or publishes."""
    path=db.DATA/'v6_allocation/study.json'
    return json.loads(path.read_text()) if path.exists() else {'status':'not_started','latestPreview':None,'periods':{}}
