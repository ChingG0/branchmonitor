"""V6 finite-grid short-only TX research. No application/database writes."""
import json,math,hashlib,csv
from pathlib import Path
from datetime import date
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1] / 'research' / 'v6_macro_20261017'

def features(rows):
 f=pd.DataFrame.from_dict(rows,orient='index').sort_index().astype(float)
 prev=f.close.shift();tr=pd.concat([f.high-f.low,(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
 f['atr']=tr.rolling(14).mean()
 for n in (5,10,20,60):
  f[f'ma{n}']=f.close.rolling(n).mean();f[f'low{n}']=f.close.shift().rolling(n).min()
 f['high20']=f.high.shift().rolling(20).max()
 f['high60']=f.high.shift().rolling(60).max()
 f['dd']=1-f.close/f.high20
 f['return_atr']=(f.close-prev)/f.atr
 return f

def signals(f,cfg):
 family=cfg['family'];level=cfg['level']
 if family=='probability':return f['prob_signal'].fillna(False)
 if family=='breakdown':
  n=[5,10,20][level]
  s=(f.close<f[f'low{n}'])&(f.close<f.ma20)&(f.ma5<f.ma5.shift())
 elif family=='failed_rally':
  n=[5,10,20][level]
  s=(f.close<f.ma20)&(f.ma20<f.ma20.shift(5))&(f.close.shift()>=f[f'ma{n}'].shift())&(f.close<f.low.shift())
 else:
  threshold=[.3,.6,.9][level]
  s=(f.close.shift().rolling(10).max()>=f.high60*.97)&(f.close<f.ma5)&(f.close.shift()>=f.ma5.shift())&(f.return_atr < -threshold)
 # No chasing after a >8% decline from the prior 20-session high.
 s=s&(f.dd<.08)&f.atr.notna()
 if cfg.get('regime')=='below60':s=s&(f.close<f.ma60)
 if cfg.get('regime')=='bear60':s=s&(f.close<f.ma60)&(f.ma20<f.ma20.shift(5))
 return s.fillna(False)

CONTRACT_CACHE={}
def target_contract(day,quotes):
 cache_key=(id(quotes),day)
 if cache_key in CONTRACT_CACHE:return CONTRACT_CACHE[cache_key]
 contracts=sorted(k for d,k in quotes if d==day)
 if len(contracts)<2:raise ValueError('missing two TX contracts '+day)
 near=contracts[0];y,m=int(near[:4]),int(near[4:]);exp=next(date(y,m,n) for n in range(15,22) if date(y,m,n).weekday()==2)
 target=contracts[1] if (exp-date.fromisoformat(day)).days<=14 else near
 CONTRACT_CACHE[cache_key]=target
 return target

def fill_exit(bar,stop,take):
 # Both touched: assume adverse stop first. Gaps use the open, not the stop.
 if bar['open']>=stop:return bar['open'],'gap_stop'
 if bar['open']<=take:return bar['open'],'gap_profit'
 if bar['high']>=stop:return stop,'stop'
 if bar['low']<=take:return take,'profit'
 return None,None

def sim(f,quotes,cfg,start,end,cost=2.):
 cooldown_days=cfg.get('cooldown_days',5)
 if isinstance(cooldown_days,bool) or not isinstance(cooldown_days,int) or cooldown_days<0:raise ValueError('cooldown_days must be a nonnegative integer')
 sig=signals(f,cfg);days=list(f.index);trades=[];curve=[];events=[];pos=None;pnl=0.;cooldown=0;rolls=0
 first=next(d for d in days if d>=start);denom=float(f.loc[first,'open'])
 def fee(price):return cost+price*.00002
 def close_pos(day,price,why):
  nonlocal pnl,pos,cooldown
  pos['gross']+=pos['anchor']-price;pos['cost']+=fee(price)
  net=pos['gross']-pos['cost'];pnl+=net
  trades.append(dict(signal_date=pos['signal_date'],entry_date=pos['entry_date'],exit_date=day,entry=pos['entry'],exit=price,contract=pos['initial_contract'],last_contract=pos['contract'],points=round(net,4),return_pct=net/pos['entry']*100,reason=why,held=pos['held'],cost_points=pos['cost']))
  pos=None;cooldown=cooldown_days
 for i,day in enumerate(days):
  if day<start or day>end:continue
  prior=days[i-1] if i else None
  entered=False;exited=False;today_contract=None
  if pos:
   bar=quotes[(day,pos['contract'])];pos['held']+=1
   if bar['open']>=pos['stop']:close_pos(day,bar['open'],'gap_stop');exited=True
   elif bar['open']<=pos['take']:close_pos(day,bar['open'],'gap_profit');exited=True
   else:
    target=target_contract(prior,quotes)
    if target!=pos['contract']:
     new=quotes[(day,target)];shift=new['open']-bar['open']
     pos['gross']+=pos['anchor']-bar['open'];pos['cost']+=fee(bar['open'])+fee(new['open'])
     pos['anchor']=new['open'];pos['stop']+=shift;pos['take']+=shift;pos['contract']=target;rolls+=1
  if not pos and not exited and cooldown==0 and prior is not None and bool(sig.loc[prior]):
   target=target_contract(prior,quotes);bar=quotes[(day,target)];pb=quotes[(prior,target)];at=float(f.loc[prior,'atr'])
   if bar['open']<pb['close']-at:
    events.append(dict(date=day,signal_date=prior,action='skip_gap_down'))
   else:
    op=bar['open'];pos=dict(signal_date=prior,entry_date=day,entry=op,anchor=op,contract=target,initial_contract=target,stop=math.ceil(op+cfg['stop']*at),take=math.floor(op-cfg['take']*at),gross=0.,cost=fee(op),held=1,mfe=0.,mae=0.)
    events.append(dict(date=day,signal_date=prior,action='short',contract=target,open=op));entered=True
  if pos:
   bar=quotes[(day,pos['contract'])];today_contract=pos['contract']
   price,why=fill_exit(bar,pos['stop'],pos['take'])
   if price is not None:close_pos(day,price,why);exited=True
   elif pos['held']>=cfg['hold']:close_pos(day,bar['close'],'time');exited=True
  mark=0.
  if pos:
   cp=quotes[(day,pos['contract'])]['close'];mark=pos['gross']+pos['anchor']-cp-pos['cost']-fee(cp)
  curve.append(dict(date=day,points=pnl+mark,value=(pnl+mark)/denom*100,position=-1 if pos else 0,active_during_day=bool(pos or entered or exited),contract=today_contract))
  if not exited:cooldown=max(0,cooldown-1)
 values=np.array([denom]+[denom+r['points'] for r in curve]);wins=sum(t['points']>0 for t in trades);loss=-sum(min(t['points'],0) for t in trades);profit=sum(max(t['points'],0) for t in trades)
 return dict(completed=len(trades),wins=wins,win_rate=wins/len(trades)*100 if trades else None,pf=profit/loss if loss else None,points=curve[-1]['points'] if curve else 0,return_pct=curve[-1]['value'] if curve else 0,max_drawdown_pct=float(np.min(values/np.maximum.accumulate(values)-1)*100),avg_trade_pct=float(np.mean([t['return_pct'] for t in trades])) if trades else None,avg_win_pct=float(np.mean([t['return_pct'] for t in trades if t['points']>0])) if wins else None,avg_loss_pct=float(np.mean([t['return_pct'] for t in trades if t['points']<=0])) if len(trades)>wins else None,exposure_pct=sum(r['active_during_day'] for r in curve)/len(curve)*100 if curve else 0,rolls=rolls,open_position=pos,curve=curve,trades=trades,events=events)

def compact(x):return {k:v for k,v in x.items() if k not in ('curve','trades','events','open_position')}
def wilson(w,n):
 if not n:return [None,None]
 z=1.96;p=w/n;a=(p+z*z/(2*n))/(1+z*z/n);h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
 return [(a-h)*100,(a+h)*100]

def bootstrap_years(trades):
 rng=np.random.default_rng(20261017);groups={y:[t for t in trades if t['entry_date'][:4]==y] for y in sorted({t['entry_date'][:4] for t in trades})};keys=list(groups);stats=[]
 for _ in range(5000):
  sample=[t for y in rng.choice(keys,len(keys),replace=True) for t in groups[y]]
  stats.append([sum(t['points']>0 for t in sample)/len(sample)*100,np.mean([t['return_pct'] for t in sample])])
 return dict(samples=5000,method='calendar-year cluster bootstrap',win_rate_95=np.percentile(np.array(stats)[:,0],[2.5,97.5]).tolist(),avg_trade_pct_95=np.percentile(np.array(stats)[:,1],[2.5,97.5]).tolist())

def main():
 raw=json.loads((ROOT/'input.json').read_text());assert not raw['failures'],raw['failures']
 assert all(all(v is not None and math.isfinite(v) and v>0 for v in r.values()) for r in raw['index'].values()),'index OHLC missing'
 f=features(raw['index']);quotes={(r['date'],r['contract']):{k:float(r[k]) for k in ('open','high','low','close')} for r in raw['futures']}
 missing=[d for d in f.index if d>='2018-01-01' and len([k for x,k in quotes if x==d])<2]
 assert not missing,missing
 # Freeze finite experiment grid BEFORE evaluating later years.
 configs=[]
 exits=[(1.5,1.5,3),(2.,1.5,5),(2.5,1.5,10),(.75,1.5,5)]
 for family in ['breakdown','failed_rally','top_turn']:
  for level in range(3):
   for j,(take,stop,hold) in enumerate(exits):configs.append(dict(key=f'{family}-{level}-{j}',family=family,level=level,take=take,stop=stop,hold=hold))
 protocol=dict(train=['2018-01-01','2022-12-31'],validation=['2023-01-01','2024-12-31'],holdout=['2025-01-01',f.index[-1]],configs=configs,selection='Train >=20 closed trades, PF>=1.15, positive net, <=60 trades, average gain/loss >=0.8. Shortlist top 3 by average net percent/trade. Validate >=8 trades, PF>=1.1, positive net. Choose highest validation expectancy among train shortlist, else retain training leader and flag failure. No holdout tuning.',cost='2 points/side plus notional transaction tax 0.00002/side; stress=5 and10 points/side.',execution='Next regular-session open; no night bars; adverse open gap exits; same-bar stop and target use stop first. Stop based on prior index ATR. 5-day cooldown, skip entry if open below prior same-contract close by >1 ATR. Within14 calendar days before standard expiry, use next-month at next open using prior-day contract; shift stops by spread; all roll costs charged.',limits='2026 examples motivated the study, so holdout is chronological retrospective validation, not genuinely untouched prospective evidence. Existing V9 history has been researched before. No leverage/margin return. No automated orders.')
 (ROOT/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2))
 rows=[]
 for cfg in configs:
  train=sim(f,quotes,cfg,*protocol['train']);rows.append(dict(config=cfg,train=compact(train)))
 def qualifies(r):
  x=r['train'];ratio=x['avg_win_pct']/abs(x['avg_loss_pct']) if x['avg_loss_pct'] else 0
  return x['completed']>=20 and x['completed']<=60 and (x['pf'] or 0)>=1.15 and x['points']>0 and ratio>=.8
 eligible=sorted([r for r in rows if qualifies(r)],key=lambda r:r['train']['avg_trade_pct'],reverse=True)
 shortlist=(eligible or sorted([r for r in rows if r['train']['completed']>=20],key=lambda r:r['train']['avg_trade_pct'],reverse=True))[:3]
 for r in shortlist:r['validation']=compact(sim(f,quotes,r['config'],*protocol['validation']))
 passing=[r for r in shortlist if r['validation']['completed']>=8 and (r['validation']['pf'] or 0)>=1.1 and r['validation']['points']>0]
 chosen=max(passing,key=lambda r:r['validation']['avg_trade_pct']) if passing else shortlist[0]
 selection=dict(config=chosen['config'],train_pass=qualifies(chosen),validation_pass=bool(passing),shortlist=shortlist)
 (ROOT/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2))
 # Selection frozen: later periods are reporting only.
 for r in rows:
  r['validation']=compact(sim(f,quotes,r['config'],*protocol['validation']))
  r['holdout']=compact(sim(f,quotes,r['config'],*protocol['holdout']))
 cfg=chosen['config'];full=sim(f,quotes,cfg,'2018-01-01',f.index[-1]);hold=sim(f,quotes,cfg,*protocol['holdout'])
 yearly={str(y):compact(sim(f,quotes,cfg,f'{y}-01-01',f'{y}-12-31')) for y in range(2018,2027)}
 audits={}
 sig=signals(f,cfg)
 for event in ['2026-07-29','2026-10-13','2026-10-15']:
  i=list(f.index).index(event);window=list(f.index[max(0,i-5):i+2]);audits[event]=dict(signals=[dict(date=d,close=float(f.loc[d,'close']),signal=bool(sig.loc[d]),dd_pct=float(f.loc[d,'dd']*100)) for d in window],trades=[t for t in full['trades'] if t['entry_date']<=event<=t['exit_date']],events=[e for e in full['events'] if e['date'] in window])
 # Invariants and no future-dependent features.
 assert all(t['signal_date']<t['entry_date']<=t['exit_date'] for t in full['trades'])
 assert all(full['trades'][i]['exit_date']<full['trades'][i+1]['entry_date'] for i in range(len(full['trades'])-1))
 assert all(p['position'] in (0,-1) for p in full['curve'])
 cutoff='2024-12-31';short=features({d:v for d,v in raw['index'].items() if d<=cutoff})
 pd.testing.assert_series_equal(signals(short,cfg),signals(f,cfg).loc[short.index])
 assert fill_exit(dict(open=100,high=110,low=90),105,95)==(105,'stop')
 assert fill_exit(dict(open=110,high=112,low=90),105,95)==(110,'gap_stop')
 stress={str(c):compact(sim(f,quotes,cfg,*protocol['holdout'],cost=c)) for c in [5,10]}
 result=dict(as_of=f.index[-1],input_hash=hashlib.sha256((ROOT/'input.json').read_bytes()).hexdigest(),protocol=protocol,selection=selection,all_variants=rows,full=full,holdout=hold,yearly=yearly,event_audits=audits,cost_stress=stress,wilson_full=wilson(full['wins'],full['completed']),wilson_holdout=wilson(hold['wins'],hold['completed']),bootstrap=bootstrap_years(full['trades']),latest_signal=bool(sig.iloc[-1]),checks=['No future features: prefix invariant','Entry strictly after signal date','No overlapping trades','Short/flat only','Same-bar adverse ordering','Adverse gap at open'])
 
 def clean(v,path='root'):
  if isinstance(v,dict):return {k:clean(x,path+'.'+str(k)) for k,x in v.items()}
  if isinstance(v,list):return [clean(x,path+'.'+str(i)) for i,x in enumerate(v)]
  if isinstance(v,float) and not math.isfinite(v):
   raise ValueError('Nonfinite result: '+path)
  return v
 result=clean(result)
 (ROOT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
 print(json.dumps(dict(selection=selection,full=compact(full),holdout=compact(hold),yearly=yearly,audits=audits,latest_signal=bool(sig.iloc[-1])),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
