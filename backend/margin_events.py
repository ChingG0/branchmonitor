"""Frozen-rule financing event diagnostics. No fitted thresholds or trading writes."""
from pathlib import Path
from datetime import date
from functools import lru_cache
import gzip,json,math
from . import margin_statistics as src,margin_analytics as a

VERSION='margin-events-6-7-1'
METHODS=('net_average','net_fifo')
MODEL_FIELDS=('maintenance_pct','below130_pct','price_effect_pp','inventory_effect_pp')
def finite(v):return isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v)
def positive(v):return finite(v) and v>0

def decomposition(previous,current,prices,method):
    """Exact change identity on a common previous-day inventory, including exits."""
    empty={'maintenance':None,'loan':None,'below130_share':None,'price_effect':None,'inventory_effect':None}
    old=a.metrics(previous,method);new=a.metrics(current,method)
    if old['maintenance'] is None or new['maintenance'] is None or not positive(old['loan']):return empty
    active=[r for r in previous if r['quantity']>0]
    if any(not positive(prices.get(r['symbol'])) for r in active):return empty
    mark=sum(r['quantity']*1000*prices[r['symbol']] for r in active)
    price=mark/old['loan']-old['maintenance']
    inventory=new['maintenance']-old['maintenance']-price
    return {'maintenance':new['maintenance'],'loan':new['loan'],'below130_share':new['below130_share'],'price_effect':price,'inventory_effect':inventory}

def quantile90(values):
    if not values or any(not finite(v) for v in values):return None
    ordered=sorted(values);x=(len(ordered)-1)*.9;lo=int(x);hi=min(lo+1,len(ordered)-1)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(x-lo)

def two_year_floor(day):
    d=date.fromisoformat(day)
    try:return d.replace(year=d.year-2).isoformat()
    except ValueError:return d.replace(year=d.year-2,day=28).isoformat()

def reference(values,dates,i):
    floor=two_year_floor(dates[i])
    indexes=[j for j in range(i) if dates[j]>=floor]
    # No shortened warmup; the archive must establish both the left boundary and every feature.
    if floor<=dates[0] or not indexes:return {'threshold':None,'n':len(indexes),'valid':False,'reason':'近兩年參考窗尚不完整'}
    samples=[values[j] for j in indexes]
    if any(not finite(v) for v in samples):return {'threshold':None,'n':len(indexes),'valid':False,'reason':'近兩年參考窗含缺值或未通過資格'}
    return {'threshold':quantile90(samples),'n':len(samples),'valid':True,'reason':None}

def five_day_decline(values,i):
    if i<5 or any(not positive(v) for v in values[i-5:i+1]):return None
    return 1-values[i]/values[i-5]

def positive_tail(value,ref):
    if not finite(value) or not ref['valid']:return None
    return value>0 and value>=ref['threshold']

def consensus(values):
    if any(v is None for v in values) or len(set(values))!=1:return None
    return values[0]

def cleaning(inventory5,decline5,inventory_ref,decline_ref):
    left=positive_tail(inventory5,inventory_ref);right=positive_tail(decline5,decline_ref)
    return None if left is None or right is None else left and right

def breadth(current,history):
    """Bounds keep missing/IPO histories in the current ordinary-stock denominator."""
    n=len(current);above=0;unknown=0
    for symbol,close in current.items():
        past=[x.get(symbol) for x in history[-19:]]
        if len(past)!=19 or not positive(close) or any(not positive(p) for p in past):unknown+=1;continue
        above+=close>sum(past+[close])/20
    lo=above/n if n else None;hi=(above+unknown)/n if n else None
    condition=None if lo is None else True if hi<.1 else False if lo>=.1 else None
    return {'above':above,'unknown':unknown,'universe':n,'lower':lo,'upper':hi,'below10':condition}

def divergence(index_now,index_prior,maintenance,maintenance_prior,lowshare,lowshare_prior,breadth_now):
    if not positive(index_now) or len(index_prior)!=20 or any(not positive(x) for x in index_prior):return None
    if len(maintenance_prior)!=20 or len(lowshare_prior)!=20 or not finite(maintenance) or not finite(lowshare) or any(not finite(x) for x in maintenance_prior+lowshare_prior):return None
    if breadth_now.get('below10') is None:return None
    return index_now<min(index_prior) and maintenance>min(maintenance_prior) and lowshare<max(lowshare_prior) and breadth_now['below10']

def consecutive(flags):
    out=[];count=0
    for flag in flags:
        if flag is None:count=0;out.append(None)
        elif flag:count+=1;out.append(count)
        else:count=0;out.append(0)
    return out

def episodes(flags):
    result=[];start=None
    for i,flag in enumerate(flags+[None]):
        if flag is True:
            if start is None:start=i
        elif start is not None:
            result.append({'start':start,'end':i-1,'closure':'ongoing' if i==len(flags) else 'unknown' if flag is None else 'ended'});start=None
    return result

def outcome(closes,start,horizon):
    end=start+horizon
    if end>=len(closes):return {'horizon':horizon,'status':'immature','return_pct':None,'minimum_close_return_pct':None}
    path=closes[start:end+1]
    if any(not positive(v) for v in path):return {'horizon':horizon,'status':'missing','return_pct':None,'minimum_close_return_pct':None}
    return {'horizon':horizon,'status':'ready','return_pct':(path[-1]/path[0]-1)*100,'minimum_close_return_pct':(min(path)/path[0]-1)*100}

def event_cards(series,flags,market,rule):
    grouped=episodes(flags);out=[];closes=[r['index_close'] for r in series];n=len(series)
    for k,e in enumerate(grouped):
        s,end=e['start'],e['end'];anchor=series[s]['index_close'];left=max(0,s-20);right=min(n-1,end+20)
        overlap=any(j!=k and max(left,max(0,x['start']-20))<=min(right,min(n-1,x['end']+20)) for j,x in enumerate(grouped))
        points=[]
        for i in range(left,right+1):
            r=series[i];close=r['index_close'];loan=r['official_loan'];prior=series[i-1]['official_loan'] if i else None
            points.append({'date':r['date'],'relative_day':i-s,'phase':'pre' if i<s else 'trigger' if i<=end else 'after','official_loan':loan,'loan_change1':loan/prior-1 if positive(loan) and positive(prior) else None,'decline5':r['decline5'],'index_close':close,'index_return_pct':(close/anchor-1)*100 if positive(close) and positive(anchor) else None,'breadth':r['breadth'],**{name:None for name in MODEL_FIELDS}})
        available=min(20,n-1-end)
        missing_index=sum(not positive(p['index_close']) for p in points);missing_loan=sum(not positive(p['official_loan']) for p in points)
        fullwindow=available==20 and s-left==20 and missing_index==0 and missing_loan==0
        maturity='ongoing' if e['closure']=='ongoing' else 'interrupted' if e['closure']=='unknown' else 'immature' if available<20 else 'missing' if not fullwindow else 'complete'
        out.append({'id':market+'/'+rule+'/'+series[s]['date'],'market':market,'rule':rule,'first':series[s]['date'],'last':series[end]['date'],'trigger_days':end-s+1,'closure':e['closure'],'maturity':maturity,'pre_days':s-left,'post_days':available,'missing_index_days':missing_index,'missing_loan_days':missing_loan,'overlap':overlap,'decline5_at_start':series[s]['decline5'],'threshold_at_start':series[s]['reference']['threshold'],'reference_n':series[s]['reference']['n'],'anchor_close':anchor,'outcomes':[outcome(closes,s,h) for h in (5,10,20)],'points':points})
    return list(reversed(out))

def analyze(dates,loans,indexes,breadths,models=None,eligible=None):
    """Pure prefix-stable daily decisions. Models must already pass source gates."""
    n=len(dates)
    if dates!=sorted(set(dates)) or any(len(v)!=n for v in (loans,indexes,breadths)):raise ValueError('Calendar or series alignment mismatch')
    eligible=eligible if eligible is not None else [False]*n
    if len(eligible)!=n:raise ValueError('Eligibility length mismatch')
    model=models or {k:[{} for _ in dates] for k in METHODS}
    if any(len(model[k])!=n for k in METHODS):raise ValueError('Model length mismatch')
    declines=[five_day_decline(loans,i) for i in range(n)];series=[]
    method_features={}
    for k in METHODS:
        state=model[k];debt=[r.get('loan') if eligible[i] else None for i,r in enumerate(state)];chip5=[]
        for i in range(n):
            window=[state[j].get('inventory_effect') if eligible[j] and j>0 and eligible[j-1] else None for j in range(max(0,i-4),i+1)]
            chip5.append(sum(window) if len(window)==5 and all(finite(x) for x in window) else None)
        method_features[k]={'chip5':chip5,'decline5':[five_day_decline(debt,i) for i in range(n)]}
    for i,day in enumerate(dates):
        ref=reference(declines,dates,i);watch=positive_tail(declines[i],ref)
        clean=[];div=[]
        for k in METHODS:
            mf=method_features[k];s=model[k]
            clean.append(cleaning(mf['chip5'][i],mf['decline5'][i],reference(mf['chip5'],dates,i),reference(mf['decline5'],dates,i)) if eligible[i] else None)
            valid=i>=20 and all(eligible[i-20:i+1])
            div.append(divergence(indexes[i],indexes[i-20:i],s[i].get('maintenance'),[r.get('maintenance') for r in s[i-20:i]],s[i].get('below130_share'),[r.get('below130_share') for r in s[i-20:i]],breadths[i]) if valid else None)
        series.append({'date':day,'official_loan':loans[i],'index_close':indexes[i],'breadth':breadths[i],'decline5':declines[i],'reference':ref,'watch':watch,'eligible':eligible[i],'cleaning':consensus(clean),'divergence':consensus(div)})
    counts=consecutive([r['divergence'] for r in series])
    for r,count in zip(series,counts):r['divergence_streak']=count
    return series

def frame(series,market):
    latest=series[-1];n=len(series);flags=[r['watch'] for r in series]
    cases=event_cards(series,flags,market,'official_decline5')
    return {'market':market,'date':latest['date'],'official_days':sum(positive(r['official_loan']) for r in series),'index_days':sum(positive(r['index_close']) for r in series),'breadth_days':sum(r['breadth']['universe']>0 for r in series),'eligible_model_days':sum(r['eligible'] for r in series),'days':n,
        'primary':{'cleaning':{'status':'blocked','confirmed_count':None,'events':[],'latest_signal':None},'divergence':{'status':'blocked','confirmed_count':None,'events':[],'latest_signal':None,'consecutive_days':None}},
        'observation':{'kind':'official_market_total_only','label':'官方總融資極端減少觀測（含ETF；非已確認清洗）','eligible_days':sum(r['watch'] is not None for r in series),'trigger_days':sum(r['watch'] is True for r in series),'episodes':cases,'latest_decline5':latest['decline5'],'latest_reference':latest['reference'],'latest_breadth':latest['breadth'],'latest_index':latest['index_close']},
        'blockers':['包2本金、暖機及方法敏感度尚未通過；清洗與背離皆不可確認','近兩年參考窗必須完整，缺值不算安全，也不當成0次事件','指數與每日行情以實際覆蓋為準；不以加權指數代替櫃買缺漏']}

@lru_cache(maxsize=2)
def _load(path,mtime,size):return json.loads(gzip.decompress(Path(path).read_bytes()))
def report(day=None,*,store=None):
    path=(Path(store) if store else src.folder())/'events.json.gz'
    if not path.exists():return {'version':VERSION,'status':'missing','model_ready':False,'dates':[],'frame':None}
    stat=path.stat();v=_load(str(path),stat.st_mtime_ns,stat.st_size)
    if v.get('version')!=VERSION or v.get('model_ready') is not False:raise ValueError('包6、7尚未通過發布資格')
    chosen=day or v['dates'][-1]
    if chosen not in v['dates']:raise ValueError('該日不在封存交易日曆內')
    f=v['frames'][chosen]
    for market in f['markets']:
        for name,p in market['primary'].items():
            if p['status']!='blocked' or p['confirmed_count'] is not None or p['events'] or p['latest_signal'] is not None or name=='divergence' and p['consecutive_days'] is not None:raise ValueError('未驗收訊號不可發布')
        for e in market['observation']['episodes']:
            if e['rule']!='official_decline5' or e['last']>chosen or any(p['date']>chosen or any(p[k] is not None for k in MODEL_FIELDS) for p in e['points']):raise ValueError('觀測事件包含未驗收數值或未來資料')
            for o in e['outcomes']:
                future=v['dates'].index(e['first'])+o['horizon']>v['dates'].index(chosen)
                if future and o['status']!='immature' or o['status']!='ready' and (o['return_pct'] is not None or o['minimum_close_return_pct'] is not None):raise ValueError('未成熟事件不可包含未來結果')
    return {k:x for k,x in v.items() if k!='frames'}|{'as_of':chosen,'frame':f}
