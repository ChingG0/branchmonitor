"""Matched-minute candidate research. Requires real minute bars, never05:00 proxies.

Artifacts are produced only by explicit training CLI; read/publish never refit.
Every feature is tested by preregistered leave-one-feature-out comparisons, on
identical walk-forward dates. Failure selects M0 rather than a blank card.
"""
from __future__ import annotations
import argparse,hashlib,json,math
from datetime import datetime
from pathlib import Path
import numpy as np
from . import db,v6_evening_model as M,v6_evening_inputs as I
VERSION='v6-evening-candidate-1.0.0';ROOT=db.ROOT/'config/v6_evening/candidates'
SETTINGS={'ridge':10.,'minTrain':504,'calibrationN':126,'refitEvery':20,'testTail':504,'minTest':252,'minImprovement':.01,'defaultFeatures':['night_return_scaled']}

def sigmoid(x):return 1/(1+np.exp(-np.clip(x,-40,40)))
def logit(p):p=np.clip(p,1e-6,1-1e-6);return np.log(p/(1-p))
def fit(rows,features,h,*,min_train=504,calibration_n=126):
    # Caller supplies only already matured rows. Purge training targets that
    # overlap the calibration origin interval, separately for each horizon.
    matured=[r for r in rows if r.get('y') is not None and abs(r['y'])>1e-14]
    if len(matured)<min_train+calibration_n:return None
    cal=matured[-calibration_n:];train=[r for r in matured[:-calibration_n] if r['targetDate']<cal[0]['date']]
    if len(train)<min_train or min(sum(r['y']>=0 for r in cal),sum(r['y']<0 for r in cal))<20:return None
    X=np.array([[r['features'][k] for k in features] for r in train]);Y=np.array([r['y'] for r in train]);mean=X.mean(0);sd=X.std(0);sd=np.where(sd>1e-8,sd,1.)
    Z=np.c_[np.ones(len(X)),np.clip((X-mean)/sd,-8,8)];penalty=np.eye(Z.shape[1])*SETTINGS['ridge'];penalty[0,0]=0
    beta=np.linalg.solve(Z.T@Z+penalty,Z.T@Y);res=np.sort(Y-Z@beta)
    def raw(row):
        z=np.r_[1.,np.clip((np.array([row['features'][k] for k in features])-mean)/sd,-8,8)];center=float(z@beta)
        return float((len(res)-np.searchsorted(res,-center,side='right')+.5)/(len(res)+1))
    pc=np.array([raw(r) for r in cal]);labels=np.array([r['y']>=0 for r in cal],float);A=np.c_[logit(pc),np.ones(len(pc))];theta=np.array([1.,0.])
    # Positive-slope Platt transform, shrunk toward identity; calibration is
    # disjoint from regression training and ends before each forecast cutoff.
    for _ in range(40):
        p=sigmoid(A@theta);gradient=A.T@(p-labels)+10*(theta-np.array([1.,0.]));H=A.T@((p*(1-p))[:,None]*A)+10*np.eye(2)
        change=np.linalg.solve(H,gradient);new=theta-change;new[0]=np.clip(new[0],.05,5)
        if np.max(abs(new-theta))<1e-8:theta=new;break
        theta=new
    calibrated=sigmoid(A@theta);tails={}
    for key,mask in [('up',calibrated>=.9),('down',calibrated<=.1)]:
        chosen=labels[mask];tails[key]={'n':len(chosen),'actual':float((chosen if key=='up' else 1-chosen).mean()) if len(chosen) else None}
    return {'features':features,'mean':mean.tolist(),'std':sd.tolist(),'beta':beta.tolist(),'residuals':res.tolist(),
        'calibration':{'slope':float(theta[0]),'intercept':float(theta[1]),'n':len(cal),'tails':tails},
        'trainedThrough':train[-1]['targetDate'],'calibratedThrough':cal[-1]['targetDate'],'horizon':h}

def infer(model,features,anchor,sigma,baseline):
    keys=model['features'];z=np.r_[1.,np.clip((np.array([features[k] for k in keys])-model['mean'])/model['std'],-8,8)];center=float(z@model['beta']);res=np.array(model['residuals']);h=model['horizon'];cal=model['calibration']
    p_raw=float((len(res)-np.searchsorted(res,-center,side='right')+.5)/(len(res)+1));p=float(sigmoid(cal['slope']*logit(p_raw)+cal['intercept']))
    # Transform the entire CDF by the same monotone calibration; do not glue
    # an independently calibrated probability onto unrelated point estimates.
    probabilities=[float(1-sigmoid((logit(1-q)-cal['intercept'])/cal['slope'])) for q in (.1,.5,.9)]
    residual_q=np.quantile(res,probabilities,method='inverted_cdf');levels=anchor*np.exp((center+residual_q)*sigma*math.sqrt(h))
    if p==.5:levels[1]=anchor
    bad_tail=None
    if levels[0]>anchor:bad_tail=cal['tails']['up']
    if levels[2]<anchor:bad_tail=cal['tails']['down']
    if not M.coherence(p,anchor,levels) or (bad_tail and (bad_tail['n']<30 or bad_tail['actual'] is None or bad_tail['actual']<.9)):
        return {**baseline,'guardFallback':True,'modelSelectionReason':'此時段高信心區間缺足夠歷史校準支持，退回M0'}
    return {**baseline,'up':p,'down':1-p,'direction':M.direction(p),'model':'evening_candidate','modelLabel':'夜盤時段模型',
        'medianClose':float(levels[1]),'medianChangePoints':float(levels[1]-anchor),'q10Close':float(levels[0]),'q90Close':float(levels[2]),
        'q10ChangePoints':float(levels[0]-anchor),'q90ChangePoints':float(levels[2]-anchor),'featuresUsed':keys,'featuresNotUsed':[],
        'guardFallback':False,'coherent':True}

def features_at(prices,bars,at,features,*,sigma_override=None,macro_history=None):
    at=I.stamp(at);day=str(at.date());clock=at.strftime('%H:%M');cut=at.replace(second=0,microsecond=0)
    if day not in prices.index:return None
    prefix=prices.loc[:day];sigma=float(sigma_override if sigma_override is not None else M.Baseline(prefix).sigma[-1]);regular={}
    for r in bars:
        end=I.stamp(r['bar_end_at'])
        if str(end.date())==day and end.replace(hour=13,minute=45,second=0,microsecond=0)<=end<=end.replace(hour=13,minute=46,second=0,microsecond=0) and r.get('available_at') and I.stamp(r['available_at'])<=cut:
            if r['contract'] not in regular or end>I.stamp(regular[r['contract']]['bar_end_at']):regular[r['contract']]=r
    for contract in sorted(regular):
        n=I.at_cutoff(bars,cut.isoformat(),contract)
        if not n:continue
        values={'night_return_scaled':math.log(n['price']/regular[contract]['close'])/sigma,
            'night_range_scaled':math.log(n['high_so_far']/n['low_so_far'])/sigma,
            'log_sigma':math.log(sigma),'is_DST':float(at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).dst().total_seconds()!=0),
            'drawdown60':float(1-prefix.close.iloc[-1]/prefix.close.tail(60).max()),
            'below60MA':float(prefix.close.iloc[-1]<prefix.close.tail(60).mean())}
        if any(k.startswith('month_') for k in features):
            seasonal=I.seasonal(prices,day);values.update({'month_up':seasonal['monthUpRate'],'month_mean':seasonal['monthMeanReturn'],
                'month_early':float(seasonal['phase']=='early'),'month_late':float(seasonal['phase']=='late')})
        for key,row in I.feature_asof(macro_history or [],cut.isoformat()).items():
            value=float(row['value'])
            if math.isfinite(value):values['macro:'+key]=value
        if any(k not in values or values[k] is None for k in features):return None
        return {'date':day,'clock':clock,'contract':contract,'features':{k:values[k] for k in features},'sigma':sigma,'anchor':float(prefix.close.iloc[-1]),'quoteAt':n['quote_at']}
    return None

def replay(prices,bars,clock,features,h,*,settings=None,macro_history=None):
    settings={**SETTINGS,**(settings or {})};base=M.Baseline(prices);dates=list(prices.index);samples=[]
    by_day={}
    for bar in bars:by_day.setdefault(bar['bar_start_at'][:10],[]).append(bar)
    for i,d in enumerate(dates[:-h]):
        if i<60:continue
        row=features_at(prices,by_day.get(d,[]),d+'T'+clock+':00+08:00',features,sigma_override=base.sigma[i],macro_history=macro_history)
        if row:row.update({'originIndex':i,'targetIndex':i+h,'targetDate':dates[i+h],
            'y':float(math.log(base.close[i+h]/base.close[i])/(base.sigma[i]*math.sqrt(h))),
            'actualClose':float(base.close[i+h]),'actualUp':None if base.close[i+h]==base.close[i] else bool(base.close[i+h]>base.close[i])});samples.append(row)
    result=[];current=None;fit_index=-999
    for i,row in enumerate(samples):
        if i<len(samples)-settings['testTail']:continue
        if current is None or i-fit_index>=settings['refitEvery']:
            mature=[r for r in samples[:i] if r['targetDate']<=row['date']]
            current=fit(mature,features,h,min_train=settings['minTrain'],calibration_n=settings['calibrationN']);fit_index=i
        if current is None:continue
        if current['calibratedThrough']>row['date']:raise ValueError('校準答案沒有在本筆預測前成熟')
        m0=base.predict(row['originIndex'],h);p=infer(current,row['features'],row['anchor'],row['sigma'],m0)
        result.append({**row,**p,'originDate':row['date'],'M0Up':m0['up'],'M0Forecast':m0,'trainedThrough':current['trainedThrough'],'calibratedThrough':current['calibratedThrough'],'highVolatility':base.regime(row['originIndex'])[1],'regime':base.regime(row['originIndex'])[0]})
    return result,samples

def train(prices,bars_file,clocks,features,out,*,macro_file=None):
    # No code path accepts daily futures as a substitute for minute input.
    source=I.read_bars(bars_file)
    if source['issues']:raise ValueError('歷史分時可取得時間尚未核對；先補來源證據')
    macro_history=[]
    if macro_file:
        import csv
        with Path(macro_file).open(encoding='utf-8-sig') as f:macro_history=list(csv.DictReader(f))
    out=Path(out);out.mkdir(parents=True,exist_ok=True);artifacts=[]
    code_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # Freeze exact code/config before reading the first candidate result.
    seal={'codeSha256':code_sha,'barsSha256':source['sha256'],'features':features,'clocks':clocks,'settings':SETTINGS,'macroSha256':hashlib.sha256(Path(macro_file).read_bytes()).hexdigest() if macro_file else None}
    seal_path=out/'pre-evaluation-seal.json'
    if seal_path.exists() and json.loads(seal_path.read_text())!=seal:raise ValueError('同研究目錄規格已變，須另開版本，不覆寫')
    if not seal_path.exists():seal_path.write_text(json.dumps(seal,sort_keys=True,indent=2))
    for clock in clocks:
        family=clock[:2]
        if family not in ('18','22') or not 0<=int(clock[3:])<=30:raise ValueError('模型時刻必須在18或22窗口')
        for h in (1,2,3):
            rows,samples=replay(prices,source['rows'],clock,features,h,macro_history=macro_history);candidate=M.metrics(rows);baseline=M.metrics([{**r,**r['M0Forecast']} for r in rows]);improvement=1-candidate['brier']/baseline['brier'] if rows and baseline['brier'] else None;ablations=[]
            for feature in features:
                simpler=[k for k in features if k!=feature]
                if not simpler:loss=baseline['brier']
                else:
                    reduced,_=replay(prices,source['rows'],clock,simpler,h,macro_history=macro_history);by={r['date']:r for r in reduced};paired=[by[r['date']] for r in rows if r['date'] in by]
                    loss=M.metrics(paired)['brier'] if len(paired)==len(rows) else None
                gain=1-candidate['brier']/loss if loss and candidate['brier'] is not None else None
                ablations.append({'feature':feature,'relativeImprovement':gain,'passed':gain is not None and gain>=.01})
            eligible=bool(len(rows)>=SETTINGS['minTest'] and improvement is not None and improvement>=.01 and all(a['passed'] for a in ablations))
            mature=[r for r in samples if r['targetDate']<=str(prices.index[-1])];model=fit(mature,features,h)
            history=M.summary(rows)[h-1] if rows else None
            if history:
                history['candidate']=history['M0'];history['M0']=baseline;history['nonoverlapCandidate']=history['nonoverlap'];history['regimesCandidate']=history['regimes']
            artifact={'version':VERSION,'createdAt':datetime.now(I.TZ).isoformat(),'slot':family,'clock':clock,'horizon':h,'model':model,'historicalEligible':eligible,
                'validation':{'n':len(rows),'M0':baseline,'candidate':candidate,'relativeImprovement':improvement,'featureTests':ablations,
                    'temporalWalkForward':True,'sameDates':True,'pristineHumanBlind':False,'calibrationAndTestSeparated':True},
                'historical':history,'sourceBarsSha256':source['sha256'],'codeSha256':code_sha,
                'note':'歷史重建曾接觸同期間現貨；不宣稱全新盲測。每筆模型與校準只使用當時以前已成熟資料。'}
            raw=json.dumps(artifact,ensure_ascii=False,sort_keys=True,allow_nan=False).encode();name=hashlib.sha256(raw).hexdigest()+'.json';path=out/name
            if not path.exists():path.write_bytes(raw)
            artifacts.append({'key':family+'-'+clock.replace(':','')+'-h'+str(h),'file':str(path.resolve()),'sha256':name[:-5],'eligible':eligible})
    index={'version':VERSION,'artifacts':artifacts};(out/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2))
    # User explicitly authorized automatic use after the fixed qualification gate.
    from .v6_evening_forecast import atomic
    atomic(ROOT/'index.json',index)
    return artifacts

def inspect(clock,h,cutoff):
    path=ROOT/'index.json'
    if not path.exists():return None
    index=json.loads(path.read_text());key=clock[:2]+'-'+clock.replace(':','')+'-h'+str(h)
    item=next((a for a in index.get('artifacts',[]) if a['key']==key),None)
    if not item:return None
    raw=Path(item['file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('時段候選模型雜湊不符')
    a=json.loads(raw);v=a['validation']
    if a['version']!=VERSION or a['clock']!=clock or a['horizon']!=h:return None
    if I.stamp(a['createdAt'])>I.stamp(cutoff):return None
    a['artifactSha256']=item['sha256']
    return a

def load(clock,h,cutoff):
    a=inspect(clock,h,cutoff)
    if not a:return None
    v=a['validation']
    if a['codeSha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():return None
    if not a['historicalEligible'] or v['relativeImprovement'] is None or v['relativeImprovement']<.01 or not all(t['passed'] for t in v['featureTests']):return None
    if I.stamp(a['createdAt'])>I.stamp(cutoff) or datetime.fromisoformat(a['model']['calibratedThrough']).date()>I.stamp(cutoff).date():return None
    return a

def main():
    from . import v6_shared_distribution as D
    p=argparse.ArgumentParser(description='需真實TX分鐘資料才訓練；沒有分時不使用日線替代')
    p.add_argument('--bars',required=True,type=Path);p.add_argument('--clocks',nargs='+',default=['18:00','22:00']);p.add_argument('--features',nargs='+',default=SETTINGS['defaultFeatures']);p.add_argument('--output',required=True,type=Path);p.add_argument('--feature-history',type=Path);a=p.parse_args()
    print(json.dumps(train(D.prices(),a.bars,a.clocks,a.features,a.output,macro_file=a.feature_history),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
