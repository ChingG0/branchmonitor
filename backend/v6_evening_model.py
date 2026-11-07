"""Causal cash-only M0 distribution and common forecast evaluation.

No 05:00 futures inputs. No fitting, fetching or grading on a page read.
Direction and q10/q50/q90 all come from one volatility-scaled empirical CDF.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

THRESHOLDS=(.60,.65,.70)

def direction(p,threshold=.65):
    if not math.isfinite(float(p)) or not 0<=p<=1:raise ValueError('invalid probability')
    return 'none' if p==.5 else 'up' if p>=threshold else 'down' if 1-p>=threshold else 'none'

def sigma_series(close):
    c=np.asarray(close,float)
    if len(c)<21 or not np.isfinite(c).all() or (c<=0).any():raise ValueError('至少21筆有效收盤')
    rets=np.diff(np.log(c));out=np.full(len(c),np.nan)
    for i in range(20,len(c)):
        v=rets[max(0,i-60):i];w=.94**np.arange(len(v)-1,-1,-1)
        out[i]=max(float(np.std(v[-20:],ddof=1)),math.sqrt(float(w@(v*v)/w.sum())),1e-6)
    return out

def coherence(p,anchor,levels):
    q10,q50,q90=levels
    return bool(all(math.isfinite(x) and x>0 for x in (anchor,*levels)) and q10<=q50<=q90
        and not (p>.5 and q50<anchor-1e-7) and not(p<.5 and q50>anchor+1e-7)
        and not(q10>anchor+1e-7 and p<.9) and not(q90<anchor-1e-7 and p>.1))

class Baseline:
    def __init__(self,prices,history_n=1260,min_n=252):
        if not prices.index.is_unique or list(prices.index)!=sorted(prices.index):raise ValueError('價格日期重複或未排序')
        self.prices=prices.copy();self.dates=list(map(str,prices.index));self.close=prices.close.to_numpy(float)
        self.log=np.log(self.close);self.sigma=sigma_series(self.close);self.history_n=history_n;self.min_n=min_n
    def predict(self,at,h):
        i=self.dates.index(at) if isinstance(at,str) else at
        if h not in (1,2,3) or i<0 or i>=len(self.close):raise ValueError('期限或價格起點無效')
        # j+h<=i means the historical answer has already closed at origin T.
        j=np.arange(20,i-h+1,dtype=int)[-self.history_n:]
        if len(j)<self.min_n:raise ValueError('M0已成熟歷史不足，保留上次有效版本而非編造')
        z=(self.log[j+h]-self.log[j])/(self.sigma[j]*math.sqrt(h))
        returns=z*self.sigma[i]*math.sqrt(h)
        p=float((np.count_nonzero(returns>1e-14)+.5*np.count_nonzero(abs(returns)<=1e-14))/len(returns))
        q=np.quantile(returns,[.1,.5,.9],method='inverted_cdf')
        if p==.5:q[1]=0.
        anchor=float(self.close[i]);levels=(anchor*np.exp(q)).tolist()
        if not coherence(p,anchor,levels):raise ValueError('M0分布的機率／分位數不一致')
        return {'horizon':h,'up':p,'down':1-p,'direction':direction(p),
            'anchorClose':anchor,'medianClose':levels[1],'medianChangePoints':levels[1]-anchor,
            'q10Close':levels[0],'q90Close':levels[2],
            'q10ChangePoints':levels[0]-anchor,'q90ChangePoints':levels[2]-anchor,
            'dailySigmaPct':float(self.sigma[i]*100),'sampleN':len(j),
            'trainingFirstOrigin':self.dates[int(j[0])],'lastTrainingOutcome':self.dates[int(j[-1]+h)],
            'model':'M0','modelLabel':'目前用基準模型','coherent':True,
            'featuresUsed':['現貨已完成收盤','近期現貨波動'],
            'featuresNotUsed':['當時夜盤','海外即時行情','未驗證的其他分頁特徵']}
    def regime(self,i):
        if i<60:return 'unclassified'
        ma=float(np.mean(self.close[i-59:i+1]));old=float(np.mean(self.close[i-64:i-4])) if i>=65 else ma
        history=self.sigma[max(20,i-504):i];high=len(history)>=126 and self.sigma[i]>np.quantile(history,.8)
        trend='bull' if self.close[i]>ma and ma>=old else 'bear' if self.close[i]<ma else 'range'
        return trend, bool(high)
    def replay(self,tail=504):
        rows=[]
        for h in (1,2,3):
            start=max(20+h+self.min_n-1,len(self.dates)-h-tail)
            for i in range(start,len(self.dates)-h):
                result=self.predict(i,h);actual=float(self.close[i+h]);trend,high=self.regime(i)
                rows.append({**result,'originDate':self.dates[i],'originIndex':i,'targetDate':self.dates[i+h],
                    'targetIndex':i+h,'actualClose':actual,'actualUp':None if actual==self.close[i] else bool(actual>self.close[i]),
                    'regime':trend,'highVolatility':high,'kind':'historical_reconstruction_not_actual_publication'})
        return rows

def pinball(y,q,tau):return max(tau*(y-q),(tau-1)*(y-q))
def metrics(rows):
    checked=[r for r in rows if r.get('actualClose') is not None]
    binary=[r for r in checked if isinstance(r.get('actualUp'),bool)]
    n=len(binary);nq=len(checked);thresholds=[]
    for threshold in THRESHOLDS:
        chosen=[r for r in binary if direction(r['up'],threshold)!='none']
        correct=sum((direction(r['up'],threshold)=='up')==r['actualUp'] for r in chosen)
        thresholds.append({'threshold':threshold,'eligibleN':n,'signals':len(chosen),'correct':correct,
            'coverage':len(chosen)/n if n else None,'accuracy':correct/len(chosen) if chosen else None})
    inside=sum(r['q10Close']<=r['actualClose']<=r['q90Close'] for r in checked)
    loss={str(q):sum(pinball(r['actualClose'],r[key],q) for r in checked)/nq if nq else None for q,key in [(.1,'q10Close'),(.5,'medianClose'),(.9,'q90Close')]}
    return {'n':n,'quantileN':nq,'flatN':nq-n,'brier':sum((r['up']-int(r['actualUp']))**2 for r in binary)/n if n else None,
        'logLoss':sum(-math.log(max(1e-12,min(1-1e-12,r['up'] if r['actualUp'] else 1-r['up']))) for r in binary)/n if n else None,
        'thresholds':thresholds,'coverage80':inside/nq if nq else None,'inside80':inside,
        'lowerTailN':sum(r['actualClose']<r['q10Close'] for r in checked),
        'upperTailN':sum(r['actualClose']>r['q90Close'] for r in checked),'pinballPoints':loss}

def nonoverlapping(rows):
    out=[];last_end=-1
    for row in sorted(rows,key=lambda r:r['originIndex']):
        if row['originIndex']>=last_end:out.append(row);last_end=row['targetIndex']
    return out

def summary(rows):
    result=[]
    for h in (1,2,3):
        sample=[r for r in rows if r['horizon']==h];independent=nonoverlapping(sample)
        result.append({'horizon':h,'first':sample[0]['originDate'] if sample else None,'last':sample[-1]['originDate'] if sample else None,
            'M0':metrics(sample),'nonoverlap':metrics(independent),'nonoverlapOrigins':[r['originDate'] for r in independent],
            'regimes':{key:metrics([r for r in sample if r['regime']==key]) for key in ('bull','bear','range')},
            'highVolatility':metrics([r for r in sample if r['highVolatility']])})
    return result

def select_model(*,historical_eligible,shadow_rows,source_qualified=True):
    """Always keep shadow predictions so automatic recovery is not selection-biased."""
    if not historical_eligible or not source_qualified:return 'M0'
    paired=[r for r in shadow_rows if r.get('candidateUp') is not None and isinstance(r.get('actualUp'),bool) and r.get('qualified')][-60:]
    if len(paired)<60:return 'candidate'
    brier=lambda key:sum((r[key]-int(r['actualUp']))**2 for r in paired)/len(paired)
    return 'M0' if brier('candidateUp')>brier('M0Up') else 'candidate'
