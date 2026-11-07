"""V6 dated risk forecasts. Explicit research/publish only; reads never train or publish.

Targets are index events, never short-trade profits. Every rolling test reserves
one year for calibration and purges labels that overlap either boundary.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / 'research/v6_macro_20261017'
ARTIFACT = ROOT / 'research/v6_forecast_20261020/results.json'
VERSION = 'v6-event-forecast-20261020'
TZ = ZoneInfo('Asia/Taipei')
TARGETS = [
    dict(key='down1', label='下一交易日收跌', horizon=1, threshold=0., kind='close'),
    dict(key='drop3', label='3 日內回落至少 2%', horizon=3, threshold=.02, kind='low'),
    dict(key='drop5', label='5 日內回落至少 3%', horizon=5, threshold=.03, kind='low'),
    dict(key='mini5', label='5 日內回落至少 5%', horizon=5, threshold=.05, kind='low'),
    dict(key='extend10', label='10 日內擴大至下一損傷級別', horizon=10, kind='extension'),
    dict(key='rebound5', label='5 日內出現收紅且收復前日高點', horizon=5, kind='rebound'),
]
CORE = ['ret1','ret5','bias20','bias60','vol20','vol_ratio','amplitude','dd20','rsi','rsi_change3']
CHANGE = CORE + ['amplitude_change','negative_share','cp_recent']
MACRO = CHANGE + ['vix_value','vix_r1','vix_r5','spx_r1','spx_r5','tsm_r1','twd_r5','oil_r5','rates-proxy_change5','rotation']
NAMES = {'core':'價格基準模型','change':'價格＋變點偵測','macro':'價格＋變點＋海外資料'}


def clean(x):
    if isinstance(x, dict): return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x, (list,tuple,np.ndarray)): return [clean(v) for v in x]
    if isinstance(x, np.generic): return clean(x.item())
    if isinstance(x, float) and not math.isfinite(x): return None
    return x


def read_artifact(path=None):
    try:
        r=json.loads((path or ARTIFACT).read_text())
        return r if r.get('version') == VERSION else None
    except (OSError,ValueError): return None


def change_probability(values, hazard=1/60, cap=120):
    """Student-t Bayesian online run-length filter; P(run length <= 3).
    Uses only current and preceding observations. This is NOT a crash probability.
    """
    probs=np.array([1.]);mu=np.array([0.]);k=np.array([1.]);a=np.array([2.]);b=np.array([2.])
    result=[]
    for value in values:
        if not np.isfinite(value): result.append(np.nan);continue
        df=2*a;scale=np.sqrt(b*(k+1)/(a*k))
        def density(m,df,s):
            return np.exp(np.array([math.lgamma((d+1)/2)-math.lgamma(d/2) for d in df])
                -.5*np.log(df*np.pi)-np.log(s)-(df+1)/2*np.log1p(((value-m)/s)**2/df))
        pred=density(mu,df,scale)
        prior=float(density(np.array([0.]),np.array([4.]),np.array([math.sqrt(2.)]))[0])
        weights=np.r_[hazard*prior,probs*pred*(1-hazard)]
        weights=weights[:cap];weights/=weights.sum()
        new_mu=(k*mu+value)/(k+1)
        new_b=b+k*(value-mu)**2/(2*(k+1))
        mu=np.r_[value/2,new_mu][:cap];k=np.r_[2.,k+1][:cap]
        a=np.r_[2.5,a+.5][:cap];b=np.r_[2.+value**2/4,new_b][:cap]
        probs=weights
        result.append(float(probs[:4].sum()))
    return np.array(result)


def features(frame):
    f=frame.copy().sort_index()
    f['ret1']=f.close.pct_change();f['ret5']=f.close.pct_change(5)
    f['bias20']=f.close/f.close.rolling(20).mean()-1
    f['bias60']=f.close/f.close.rolling(60).mean()-1
    f['vol20']=f.ret1.rolling(20).std()
    f['vol_ratio']=f.ret1.rolling(5).std()/f.vol20
    f['amplitude']=(f.high-f.low)/f.close.shift()
    f['amplitude_change']=f.amplitude.rolling(3).mean()/f.amplitude.rolling(20).mean()-1
    f['negative_share']=(f.ret1<0).rolling(5).mean()
    f['dd20']=(1-f.close/f.high.rolling(20).max()).clip(lower=0)
    delta=f.close.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    f['rsi']=100-100/(1+up/down.replace(0,np.nan));f['rsi_change3']=f.rsi.diff(3)
    logamp=np.log(f.amplitude.clip(lower=1e-6))
    standardized=(logamp-logamp.shift().rolling(60).mean())/logamp.shift().rolling(60).std()
    f['cp_recent']=change_probability(standardized.clip(-6,6).to_numpy())
    if 'financial_r5' in f and 'electronics_r5' in f:
        f['rotation']=f.financial_r5-f.electronics_r5
    return f.replace([np.inf,-np.inf],np.nan)


def targets(f,spec):
    h=spec['horizon'];out=pd.DataFrame(index=f.index)
    close=f.close.to_numpy();low=f.low.to_numpy();dates=list(f.index)
    ys=[];ends=[];minret=[];eligible=[];barriers=[]
    for i in range(len(f)):
        end=i+h
        valid=end<len(f)
        kind=spec['kind'];dd=float(f.dd20.iloc[i]);barrier=None
        active=kind!='extension' or 0.05<=dd<.30
        if kind=='extension' and active:
            level=next(v for v in [.10,.20,.30] if v>dd)
            barrier=float(f.high.iloc[max(0,i-19):i+1].max())*(1-level)
        if not valid or not active:
            ys.append(np.nan);ends.append(None);minret.append(np.nan);eligible.append(active);barriers.append(barrier);continue
        future_low=float(np.min(low[i+1:end+1]));minimum=future_low/close[i]-1
        if kind=='close': y=close[end]<close[i]
        elif kind=='low': y=minimum<=-spec['threshold']
        elif kind=='extension': y=future_low<=barrier
        else:
            y=any(close[j]>f.open.iloc[j] and close[j]>f.high.iloc[j-1] for j in range(i+1,end+1))
        ys.append(float(y));ends.append(dates[end]);minret.append(minimum);eligible.append(active);barriers.append(barrier)
    out['y']=ys;out['end']=ends;out['minimum_return']=minret;out['eligible']=eligible;out['barrier']=barriers
    return out


def fit_logit(x,y,penalty=.1):
    x=np.asarray(x,float);y=np.asarray(y,float)
    med=np.nanmedian(x,axis=0);sd=np.nanstd(x,axis=0);sd=np.where(sd<1e-8,1,sd)
    z=np.clip((x-med)/sd,-5,5);z=np.c_[np.ones(len(z)),z]
    beta=np.zeros(z.shape[1]);beta[0]=math.log((y.sum()+1)/(len(y)-y.sum()+1))
    reg=np.r_[0.,np.full(z.shape[1]-1,penalty)]
    for _ in range(40):
        p=1/(1+np.exp(-np.clip(z@beta,-30,30)))
        grad=z.T@(p-y)/len(y)+reg*beta
        hessian=(z.T*(p*(1-p)))@z/len(y)+np.diag(reg+1e-8)
        step=np.linalg.solve(hessian,grad);beta-=step
        if np.max(np.abs(step))<1e-7:break
    return clean(dict(median=med,sd=sd,beta=beta))


def predict(model,x):
    z=np.clip((np.asarray(x,float)-model['median'])/model['sd'],-5,5)
    return 1/(1+np.exp(-np.clip(np.c_[np.ones(len(z)),z]@model['beta'],-30,30)))


def calibrate_fit(p,y):
    y=np.asarray(y,float);p=np.clip(p,1e-5,1-1e-5)
    if len(y)<100 or min(y.sum(),len(y)-y.sum())<20:return None
    # Positive slope only: preserve ranking; small calibration sets cannot invert signals.
    x=np.log(p/(1-p));b=np.array([0.,1.])
    for _ in range(40):
        z=np.c_[np.ones(len(x)),x];q=1/(1+np.exp(-np.clip(z@b,-30,30)))
        grad=z.T@(q-y)/len(y)+np.array([0.,.02*(b[1]-1)])
        h=(z.T*(q*(1-q)))@z/len(y)+np.diag([1e-8,.02])
        new=b-np.linalg.solve(h,grad);new[1]=np.clip(new[1],.05,5.)
        if np.max(np.abs(new-b))<1e-7:b=new;break
        b=new
    return clean(b)


def calibrated(p,coef):
    if coef is None:return p
    q=np.clip(p,1e-5,1-1e-5)
    return 1/(1+np.exp(-np.clip(coef[0]+coef[1]*np.log(q/(1-q)),-30,30)))


def metrics(rows, probability='p'):
    if not rows:return None
    y=np.array([r['y'] for r in rows]);p=np.array([r[probability] for r in rows]);base=np.array([r['base'] for r in rows])
    brier=float(np.mean((p-y)**2));bb=float(np.mean((base-y)**2))
    return dict(n=len(rows),events=int(y.sum()),event_rate_pct=round(float(y.mean()*100),1),
        brier=round(brier,5),baseline_brier=round(bb,5),brier_skill_pct=round((1-brier/bb)*100,2) if bb else None,
        skill_range_95=block_interval(p,y,base))


def block_interval(p,y,base):
    # 20-row blocks preserve nearby overlapping targets; exploratory, not IID bounds.
    rng=np.random.default_rng(620);blocks=[np.arange(i,min(i+20,len(y))) for i in range(0,len(y),20)]
    if len(blocks)<10:return None
    stats=[]
    for _ in range(400):
        ix=np.concatenate([blocks[k] for k in rng.integers(0,len(blocks),len(blocks))]);bb=np.mean((base[ix]-y[ix])**2)
        if bb:stats.append((1-np.mean((p[ix]-y[ix])**2)/bb)*100)
    return [round(float(v),2) for v in np.quantile(stats,[.025,.975])] if stats else None


def availability(f,cols):
    result=f[cols].notna().all(axis=1)
    if 'vix_value' not in cols:return result
    decision=pd.to_datetime(f['decision_at'],utc=True)
    for key in ['vix','spx','tsm','twd','oil','rates-proxy','financial','electronics']:
        available=pd.to_datetime(f[key+'_available'],utc=True)
        result &= available.notna() & (available<=decision) & ((decision-available)<=pd.Timedelta(days=7))
    return result


def range_research(f):
    """Volatility-scaled empirical distribution, not a fitted CAViaR model."""
    t=targets(f,TARGETS[2]);scale=f.vol20*np.sqrt(5)
    z=t.minimum_return/scale;valid=z.notna() & (scale>0)
    rows=[]
    for year in range(2021,int(f.index[-1][:4])+1):
        start=f'{year}-01-01';end=f'{year+1}-01-01'
        train=valid&(f.index<start)&(t.end.fillna('9999')<start)&(f.index>=f'{year-3}-01-01')
        test=valid&(f.index>=start)&(f.index<end)
        if train.sum()<200:continue
        q=np.quantile(z[train],[.1,.5,.9])
        for day in f.index[test]:rows.append((float(t.at[day,'minimum_return']),q*scale.loc[day]))
    current=valid&(f.index>=f'{int(f.index[-1][:4])-3}-01-01')
    qs=np.quantile(z[current],[.1,.5,.9]) if current.sum()>=200 else None
    return clean(dict(method='近三年已到期最低報酬，以當時20日波動標準化，再乘目前波動；不是保證區間。',
        n=len(rows),coverage_10_pct=np.mean([v<=q[0] for v,q in rows])*100 if rows else None,
        coverage_90_pct=np.mean([v<=q[2] for v,q in rows])*100 if rows else None,scaled_quantiles=qs,
        levels=[.1,.5,.9]))


def episode_metrics(rows,horizon):
    """Merge threshold-hit dates within the horizon; only pre-onset alerts count."""
    if not rows:return None
    dates=sorted(set(r['i']+r['lead'] for r in rows if r['y'] and r.get('lead') is not None))
    groups=[]
    for hit in dates:
        if not groups or hit>groups[-1][-1]+horizon:groups.append([hit])
        else:groups[-1].append(hit)
    alerts=[];last=-999
    for r in rows:
        if r['p']>=r['alarm'] and r['i']>last+horizon:alerts.append(r);last=r['i']
    hit=sum(r['y'] for r in alerts);leads=[]
    for group in groups:
        warnings=[r for r in alerts if r['y'] and r.get('lead') is not None
                  and r['i']<group[0] and r['i']+r['lead'] in group]
        if warnings:leads.append(group[0]-min(r['i'] for r in warnings))
    months=len({r['date'][:7] for r in rows})
    return dict(alerts=len(alerts),true_alerts=int(hit),false_alerts=int(len(alerts)-hit),
        precision_pct=round(hit/len(alerts)*100,1) if alerts else None,
        event_windows=len(groups),missed_windows=len(groups)-len(leads),
        false_alerts_per_month=round((len(alerts)-hit)/months,2),
        median_lead_sessions=float(np.median(leads)) if leads else None,
        note='同一觀察期限內合併警報與觸及事件；事件開始後才發出的警報不算提前捕捉。')


def evaluate(f, spec, cols, first_year=2021):
    t=targets(f,spec);rows=[];artifacts=[]
    complete=availability(f,cols)&t.y.notna()
    for year in range(first_year,int(f.index[-1][:4])+1):
        train_edge=f'{year-1}-01-01';test_edge=f'{year}-01-01';test_end=f'{year+1}-01-01'
        train=complete & (f.index<train_edge) & (t.end.fillna('9999')<train_edge)
        cal=complete & (f.index>=train_edge) & (f.index<test_edge) & (t.end.fillna('9999')<test_edge)
        test=complete & (f.index>=test_edge) & (f.index<test_end)
        if train.sum()<400 or min(t.loc[train,'y'].sum(),train.sum()-t.loc[train,'y'].sum())<20:continue
        if cal.sum()<100:continue
        m=fit_logit(f.loc[train,cols],t.loc[train,'y'])
        cp=predict(m,f.loc[cal,cols]);coef=calibrate_fit(cp,t.loc[cal,'y'])
        if coef is None:continue
        base=(float(t.loc[cal,'y'].sum())+1)/(int(cal.sum())+2)
        # Alarm threshold fixed from preceding calibration year, never the test year.
        alarm=max(base+.10,float(np.quantile(calibrated(cp,coef),.8)))
        p=predict(m,f.loc[test,cols]);pc=calibrated(p,coef)
        for day,a,b in zip(f.index[test],p,pc):
            i=f.index.get_loc(day);lead=None
            if t.at[day,'y']:
                if spec['kind']=='low': lead=next(j for j in range(1,spec['horizon']+1) if f.low.iloc[i+j]/f.close.iloc[i]-1<=-spec['threshold'])
                elif spec['kind']=='extension':lead=next(j for j in range(1,spec['horizon']+1) if f.low.iloc[i+j]<=t.at[day,'barrier'])
                elif spec['kind']=='rebound':lead=next(j for j in range(1,spec['horizon']+1) if f.close.iloc[i+j]>f.open.iloc[i+j] and f.close.iloc[i+j]>f.high.iloc[i+j-1])
                else:lead=spec['horizon']
            rows.append(dict(date=day,i=int(i),end=t.at[day,'end'],y=float(t.at[day,'y']),p=float(b),raw=float(a),base=base,alarm=alarm,lead=lead))
        artifacts.append(dict(test_year=year,train_count=int(train.sum()),calibration_count=int(cal.sum()),test_count=int(test.sum()),
            train_through=str(t.loc[train,'end'].max()),calibration_through=str(t.loc[cal,'end'].max())))
    return dict(metrics=metrics(rows),raw_metrics=metrics(rows,'raw'),episodes=episode_metrics(rows,spec['horizon']),
        bins=[dict(low=lo,high=lo+.2,n=len(a),predicted_pct=round(float(np.mean([r['p'] for r in a]))*100,1) if a else None,
                   actual_pct=round(float(np.mean([r['y'] for r in a]))*100,1) if a else None)
              for lo in [0.,.2,.4,.6,.8] for a in [[r for r in rows if lo<=r['p']<lo+.2]]],
        years=[dict(year=y,**(metrics([r for r in rows if r['date'].startswith(str(y))]) or {})) for y in sorted({int(r['date'][:4]) for r in rows})],folds=artifacts,rows=rows)


def fit_current(f,spec,cols):
    t=targets(f,spec);edge=f'{int(f.index[-1][:4])-1}-01-01'
    valid=availability(f,cols)&t.y.notna()
    tr=valid&(f.index<edge)&(t.end.fillna('9999')<edge);ca=valid&(f.index>=edge)
    if tr.sum()<400 or ca.sum()<100 or min(t.loc[tr,'y'].sum(),tr.sum()-t.loc[tr,'y'].sum())<20:return None
    m=fit_logit(f.loc[tr,cols],t.loc[tr,'y']);coef=calibrate_fit(predict(m,f.loc[ca,cols]),t.loc[ca,'y'])
    if coef is None:return None
    return dict(model=m,calibration=coef,columns=cols,training_through=t.loc[tr,'end'].max(),
        calibration_through=t.loc[ca,'end'].max(),baseline=(t.loc[ca,'y'].sum()+1)/(ca.sum()+2))


def research(frame,output):
    f=features(frame);results=[]
    for spec in TARGETS:
        candidates={};raw_rows={}
        for key,cols in [('core',CORE),('change',CHANGE),('macro',MACRO)]:
            if any(c not in f for c in cols):continue
            result=evaluate(f,spec,cols)
            # Never select a candidate on these displayed test results.
            raw_rows[key]=result.pop('rows');result['current_fit']=fit_current(f,spec,cols)
            candidates[key]=result
        common=set.intersection(*(set(r['date'] for r in rows) for rows in raw_rows.values())) if raw_rows else set()
        matched={key:metrics([r for r in rows if r['date'] in common]) for key,rows in raw_rows.items()}
        results.append(dict(**spec,candidates=candidates,matched_dates=matched))
    artifact=dict(version=VERSION,created_at=datetime.now(TZ).isoformat(),data_through=f.index[-1],
        input_sha256=hashlib.sha256((SEED/'features.csv').read_bytes()).hexdigest(),
        evidence='時間滾動歷史診斷；已知歷史曾參與研究，並非完全未接觸樣本外證據。',
        protocol={'fit':'固定 L2 邏輯迴歸，懲罰0.1；不挑最佳回測門檻','split':'每年以前一完整年校準，更早資料訓練，該年測試；跨界目標剔除。',
          'promotion':'盤後固定價格基準；08:30至09:00盤前採海外模型且來源需到齊。依時間選模型，不依當天分數選；技能區間下緣未正或事件不足50筆不發布機率。變點模型獨立觀察。',
          'probability':'連續分數經獨立校準期作正斜率 sigmoid 校準；未使用測試期校準。',
          'anchor':'訊號日收盤；3/5日包含期間最低點；回撤擴大使用訊號日已知20日高點固定錨點。',
          'availability':'收盤模型僅日盤已知價格。海外模型限當筆08:30決策時點之前來源；新預報逐項核對時間。'},
        targets=results,downside_range=range_research(f),sources=[
          {'title':'Bayesian Online Changepoint Detection','url':'https://arxiv.org/abs/0710.3742'},
          {'title':'Probability calibration','url':'https://proceedings.mlr.press/v70/guo17a.html'},
          {'title':'CAViaR：尾部風險研究參考（本版未宣稱實作）','url':'https://www.simonemanganelli.org/Simone/Research_files/caviarPublished.pdf'},
          {'title':'Geopolitical Risk：新聞編碼研究參考','url':'https://www.federalreserve.gov/econres/ifdp/measuring-geopolitical-risk.htm'}])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(clean(artifact),ensure_ascii=False,allow_nan=False,indent=2))
    return artifact


def index_frame(data_dir=None):
    from . import db
    data_dir=data_dir or db.DATA
    raw=json.loads((SEED/'input.json').read_text())['index']
    path=data_dir/'monitor.sqlite3'
    if path.exists():
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as c:
            for day,op,hi,lo,cl in c.execute("SELECT date,open,high,low,close FROM prices WHERE symbol='TAIEX' AND date>?",(max(raw),)):
                if all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in [op,hi,lo,cl]):
                    raw[day]=dict(open=op,high=hi,low=lo,close=cl)
    return pd.DataFrame.from_dict(raw,orient='index').sort_index().astype(float)


def morning_frame(raw):
    from . import v6_features,v6_tx
    with v6_tx._FEATURE_LOCK:
        previous=v6_features.RAW
        try:
            v6_features.RAW=v6_tx.LIVE if v6_tx.LIVE.exists() else SEED/'raw'
            return features(v6_features.build({'index':raw.to_dict(orient='index')}))
        finally:v6_features.RAW=previous


def observations(f,data_dir):
    rows=[];last=f.index[-1]
    for key,label,scale,unit in [('cp_recent','最近3日狀態轉變後驗值',100,'%'),('amplitude_change','3日振幅相對20日變化',100,'%'),('negative_share','近5日收跌比例',100,'%'),('bias20','20日乖離',100,'%')]:
        v=f[key].iloc[-1]
        rows.append(dict(label=label,value=round(float(v)*scale,2) if np.isfinite(v) else None,unit=unit,as_of=last,
            note='狀態轉變不等於下跌；需合看方向。' if key=='cp_recent' else '觀察值，不直接加成機率。'))
    path=data_dir/'monitor.sqlite3'
    if path.exists():
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as c:
            prices=pd.read_sql_query("SELECT date,symbol,close FROM prices WHERE date<=? AND date>=? AND length(symbol)=4 AND symbol NOT GLOB '*[^0-9]*' AND symbol NOT LIKE '0%' ORDER BY symbol,date",c,params=(last,str((pd.Timestamp(last)-pd.Timedelta(days=130)).date())))
        if not prices.empty:
            prices['ma20']=prices.groupby('symbol').close.transform(lambda x:x.rolling(20).mean())
            q=prices[prices.ma20.notna()].copy();q['above']=q.close>q.ma20
            broad=q.groupby('date').agg(rate=('above','mean'),n=('above','size')).sort_index()
            if last in broad.index and broad.at[last,'n']>=500:
                rows.append(dict(label='個股站上20MA比例',value=round(broad.at[last,'rate']*100,1),unit='%',as_of=last,n=int(broad.at[last,'n']),note='可得個股等權廣度；歷史僅今年，尚未納入長期機率。'))
                if len(broad)>=6:rows.append(dict(label='廣度5日變化',value=round((broad.rate.iloc[-1]-broad.rate.iloc[-6])*100,1),unit='百分點',as_of=last,note='涵蓋股票數可能隨日期改變；僅觀察。'))
    try:
        d=json.loads((data_dir/'market_margin_cache.json').read_text())['days'];days=sorted(x for x in d if x<=last)
        if len(days)>=6:
            a,b=days[-6],days[-1];ratio=d[b]['financing_units']/d[a]['financing_units']-1
            rows.append(dict(label='融資餘額5日變化',value=round(ratio*100,2),unit='%',as_of=b,note='資料未對齊' if b!=last else '僅約一年歷史，暫列觀察；不直接改機率。'))
    except (OSError,KeyError,ValueError,ZeroDivisionError):pass
    return rows


def session_dates(basis,count,data_dir=None):
    """Read the saved exchange holiday calendar; never fetch or alter it."""
    from . import db
    data_dir=data_dir or db.DATA;path=data_dir/'research.sqlite3';calendars={}
    if path.exists():
        try:
            with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as c:
                calendars={int(y):json.loads(v) for y,v in c.execute('SELECT year,payload FROM bt_calendars')}
        except (sqlite3.Error,ValueError):pass
    found=[];day=pd.Timestamp(basis)
    for _ in range(count*8+40):
        day+=pd.Timedelta(days=1);name=day.strftime('%Y-%m-%d');cal=calendars.get(day.year)
        if cal is None:
            # Fail closed for new live forecasts when the official calendar is absent.
            if data_dir==db.DATA and day.year>=datetime.now(TZ).year:return None
            opened=day.weekday()<5
        else:opened=name in cal.get('opened',[]) or (day.weekday()<5 and name not in cal.get('closed',[]))
        if opened:found.append(name)
        if len(found)==count:return found
    return None


def freeze(market_as_of,prepared_at, *, artifact=None, frame=None, data_dir=None):
    from . import db
    artifact=artifact or read_artifact();data_dir=data_dir or db.DATA
    if not artifact:return {'version':VERSION,'status':'unavailable','reason':'尚未完成預報研究'}
    stamp=pd.Timestamp(prepared_at)
    if stamp.tzinfo is None:raise ValueError('預報保存時間需含時區')
    cutoff=pd.Timestamp(market_as_of+'T16:00:00+08:00')
    if stamp<cutoff:return {'version':VERSION,'status':'unavailable','reason':'所選日收盤資料尚未達可用時間'}
    if stamp<pd.Timestamp(artifact['created_at']) or market_as_of<=artifact['data_through']:
        return {'version':VERSION,'status':'unavailable','reason':'模型建立前的日期不能倒填成當時預報'}
    raw=index_frame(data_dir) if frame is None else frame
    raw=raw.loc[raw.index<=market_as_of]
    if raw.empty or raw.index[-1]!=market_as_of:return {'version':VERSION,'status':'unavailable','reason':'所選日缺收盤行情'}
    # Conservative next-session boundary; no intraday nowcast disguised as a pre-open forecast.
    sessions=session_dates(market_as_of,1,data_dir)
    if not sessions:return {'version':VERSION,'status':'unavailable','reason':'官方開休市日曆尚未齊全'}
    next_day=sessions[0]
    if stamp>=pd.Timestamp(next_day+'T09:00:00+08:00'):
        return {'version':VERSION,'status':'unavailable','reason':'預報基準後的日盤已開始；請等待最新收盤，避免把已發生行情算成預測'}
    family='macro' if stamp>=pd.Timestamp(next_day+'T08:30:00+08:00') else 'core'
    if family=='macro':
        try:f=morning_frame(raw)
        except (OSError,ValueError,KeyError):return {'version':VERSION,'status':'unavailable','reason':'盤前來源尚未齊全，保留上一份預報'}
        if not availability(f,MACRO).iloc[-1] or pd.Timestamp(f.decision_at.iloc[-1])>stamp or any((stamp-pd.Timestamp(f[k+'_available'].iloc[-1])).total_seconds()>4*86400 for k in ['vix','spx','tsm','twd','oil','rates-proxy']):
            return {'version':VERSION,'status':'unavailable','reason':'盤前來源時間或完整性未通過檢查'}
    else:f=features(raw)
    out=[]
    for target in artifact['targets']:
        spec={k:target[k] for k in ['key','label','horizon','kind']};spec['threshold']=target.get('threshold',0)
        candidate=target['candidates'].get(family,{});fit=candidate.get('current_fit');m=candidate.get('metrics') or {}
        ready=bool(fit and m.get('brier_skill_pct',-1)>0 and m.get('events',0)>=50 and bool(m.get('skill_range_95')) and m['skill_range_95'][0]>0)
        reason=None;value=None;t=targets(f,spec)
        if not bool(t.eligible.iloc[-1]):ready=False;reason='目前尚未處於5%至30%回撤，擴大目標不適用'
        elif not ready:reason='改善幅度尚不穩定或事件不足，暫不發布數字'
        elif f[fit['columns']].iloc[-1].isna().any():ready=False;reason='行情特徵不足'
        if ready:value=float(calibrated(predict(fit['model'],f[fit['columns']].iloc[[-1]]),fit['calibration'])[0])
        out.append(dict(**spec,probability_pct=round(value*100,1) if value is not None else None,reason=reason,
            baseline_pct=round(fit['baseline']*100,1) if fit else None,barrier=clean(t.barrier.iloc[-1])))
    return clean(dict(version=VERSION,status='research',model_created_at=artifact['created_at'],
        source_cutoffs={k:str(f[k+'_available'].iloc[-1]) for k in ['vix','spx','tsm','twd','oil','rates-proxy','financial','electronics']} if family=='macro' else {},
        model_hash=hashlib.sha256(json.dumps(artifact,sort_keys=True).encode()).hexdigest(),as_of=market_as_of,
        prepared_at=prepared_at,family=family,family_label=NAMES[family],anchor_close=float(f.close.iloc[-1]),targets=out,
        downside_range={
            'quantiles_pct':[round(float(x)*float(f.vol20.iloc[-1])*math.sqrt(5)*100,2) for x in artifact['downside_range']['scaled_quantiles']],
            'method':artifact['downside_range']['method']
        } if artifact.get('downside_range',{}).get('scaled_quantiles') is not None and np.isfinite(f.vol20.iloc[-1]) else None,
        observations=observations(f,data_dir),note='價格模型機率為研究估計；新聞與短期廣度另列，不人工加減機率。'))


def audit(snapshot,frame):
    forecast=snapshot.get('forecast') or {}
    if not forecast.get('targets'):return None
    anchor=forecast['as_of'];f=frame.sort_index()
    now=datetime.now(TZ)
    if now.hour<16:f=f.loc[f.index<now.date().isoformat()]
    if f.empty or anchor not in f.index:return {'status':'missing','targets':[]}
    i=f.index.get_loc(anchor);rows=[]
    sessions=session_dates(anchor,max(t['horizon'] for t in forecast['targets']))
    for spec in forecast['targets']:
        h=spec['horizon'];end=i+h
        row=dict(key=spec['key'],label=spec['label'],probability_pct=spec['probability_pct'])
        if sessions is None:
            row.update(status='missing',reason='官方開休市日曆尚未齊全')
        elif any(d<=f.index[-1] and d not in f.index for d in sessions[:h]):
            row.update(status='missing',target_date=sessions[h-1],reason='期間交易日缺行情，暫停核對')
        elif sessions[h-1]>f.index[-1]:row.update(status='pending',target_date=sessions[h-1],remaining_sessions=sum(d>f.index[-1] for d in sessions[:h]))
        elif not np.isclose(float(f.close.iloc[i]),forecast['anchor_close'],rtol=1e-8):
            row.update(status='revised',reason='基準收盤已修訂，暫停核對')
        else:
            end=f.index.get_loc(sessions[h-1])
            future=f.loc[sessions[:h]];minimum=float(future.low.min()/forecast['anchor_close']-1)
            if future[['open','high','low','close']].isna().any().any():
                row.update(status='missing',reason='到期行情缺漏');rows.append(row);continue
            if spec['kind']=='close':y=float(future.close.iloc[-1]<forecast['anchor_close'])
            elif spec['kind']=='low':y=float(minimum<=-spec['threshold'])
            elif spec['kind']=='extension':y=float(future.low.min()<=spec['barrier']) if spec.get('barrier') is not None else np.nan
            else:
                previous=[anchor]+sessions[:h-1]
                y=float(any(f.at[d,'close']>f.at[d,'open'] and f.at[d,'close']>f.at[prior,'high'] for d,prior in zip(sessions[:h],previous)))
            row.update(status='not_applicable' if pd.isna(y) else 'complete',target_date=f.index[end],occurred=bool(y) if pd.notna(y) else None,
                minimum_return_pct=round(minimum*100,2) if pd.notna(y) else None)
            if pd.notna(y) and spec['probability_pct'] is not None:row['brier']=(spec['probability_pct']/100-float(y))**2
        rows.append(row)
    return dict(status='available',checked_through=f.index[-1],targets=rows)


def scorecard(history,frame):
    # First issued forecast per model/date/session/target, not the best revision.
    seen=set();groups={};saved=0
    for snapshot in sorted(history,key=lambda x:x['prepared_at']):
        forecast=snapshot.get('forecast') or {}
        if not forecast.get('targets'):continue
        saved+=1;result=audit(snapshot,frame)
        for row in (result or {}).get('targets',[]):
            identity=(forecast['version'],forecast['as_of'],forecast.get('family'),row['key'])
            if identity in seen:continue
            seen.add(identity);key=(forecast['version'],forecast.get('family'),row['key'])
            group=groups.setdefault(key,dict(version=key[0],family=key[1],label=row['label'],completed=0,pending=0,withheld=0,probabilities=[],outcomes=[],errors=[]))
            if row['probability_pct'] is None:group['withheld']+=1;continue
            if row['status']=='pending':group['pending']+=1
            elif row['status']=='complete':
                group['completed']+=1;group['probabilities'].append(row['probability_pct']);group['outcomes'].append(row['occurred']);group['errors'].append(row['brier'])
    result=[]
    for group in groups.values():
        p=group.pop('probabilities');y=group.pop('outcomes');e=group.pop('errors')
        group.update(mean_probability_pct=float(np.mean(p)) if p else None,actual_rate_pct=float(np.mean(y))*100 if y else None,brier=float(np.mean(e)) if e else None)
        result.append(group)
    return clean(dict(saved=saved,rows=result,note='同日同版本同時段只核對首次預報；重疊日期仍非獨立事件，樣本少時不推論穩定準確率。'))


def research_summary(artifact=None):
    a=artifact or read_artifact()
    if not a:return None
    return {**{k:a[k] for k in ['version','created_at','data_through','evidence','protocol','sources']},
        'targets':[{**{k:t[k] for k in ['key','label','horizon','kind']},'matched_dates':t.get('matched_dates',{}),'candidates':{
            k:{x:v[x] for x in ['metrics','raw_metrics','episodes','bins','years','folds']} for k,v in t['candidates'].items()}} for t in a['targets']],
        'candidate_names':NAMES,'downside_range':{k:v for k,v in a.get('downside_range',{}).items() if k!='scaled_quantiles'}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--research',type=Path,required=True);args=parser.parse_args()
    frame=pd.read_csv(SEED/'features.csv',index_col='signal_date')
    result=research(frame,args.research)
    for t in result['targets']:
        print(t['key'],[(k,v['metrics']) for k,v in t['candidates'].items()])
