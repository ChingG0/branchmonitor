"""Beta-adjusted rankings, isolated from all trading models and their accounts.

Scores are descriptive, not significance tests or calibrated win probabilities.
"""
from __future__ import annotations
import gzip,hashlib,json,math,threading,traceback,os,uuid
from collections import defaultdict
from datetime import datetime
import numpy as np
from . import alpha_beta_sources as src

VERSION='alpha-beta-v2.1'
BENCHMARKS={'TAIEX_TR':'加權報酬指數','TPEX_TR':'櫃買報酬指數','SPY':'標普500含息ETF代理（台幣）','QQQ':'NASDAQ100含息ETF代理（台幣）','DIA':'道瓊含息ETF代理（台幣）','SOXX':'半導體含息ETF代理（台幣）'}
PROTOCOL=dict(version=VERSION,estimation_days=120,evaluation_days=60,liquidity_twd=20_000_000,
              volatility_floor=.0005,top_fraction=.1,forward_days=[20,60],commission_bp_per_side=14.25,
              slippage_bp_per_side=5,stock_sell_tax_bp=30,etf_sell_tax_bp=10,
              publication='首次發布後下一個交易日收盤為追蹤起點；不回填歷史訊號',
              continuation='60日與20日β調整累積超額均為正；60日後半段累積超額為正',
              sample='首次發布時的實際可用股票池；商品、基準與市場分組分開評估',
              alpha_hac_lags=5,alpha_interval='α±1.96×該120日回歸的HAC標準誤；未作多重比較校正',
              default_benchmark_mode='matched',default_disposition_policy='exclude_active_and_unknown',
              disposition_timing='依實際發布日判斷處置狀態，同時保存收盤日狀態；驗證只使用封存的排除旗標',
              validation='20／60日向前追蹤；固定與滾動版使用相同的目前β衡量後續超額；成本只扣投資組合報酬，不扣rank IC')
_lock=threading.Lock();_state_lock=threading.Lock()
_job=dict(status='idle',message='尚未更新',started_at=None,finished_at=None)

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False));temp.replace(path)
def publish_once(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x') as f:
            f.write(json.dumps(value,ensure_ascii=False,allow_nan=False));f.flush();os.fsync(f.fileno())
        try:os.link(temp,path);return True
        except FileExistsError:return False
    finally:temp.unlink(missing_ok=True)

def average_ranks(values):
    values=np.asarray(values);order=np.argsort(values,kind='stable');ranks=np.empty(len(values),float);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:j+=1
        ranks[order[i:j]]=(i+j-1)/2;i=j
    return ranks
def job_status():
    with _state_lock:return dict(_job)
def state(**kw):
    with _state_lock:_job.update(kw)
def read(path,default=None):
    if not path.exists():return default
    return json.loads(path.read_text())
_report_cache={};_report_lock=threading.Lock()
def load_latest():
    path=src.folder()/'latest.json'
    if not path.exists():return dict(as_of=None,rows=[],comparison=[],quality=[],protocol=PROTOCOL,validation=dict(mature=[]))
    key=(str(path),path.stat().st_mtime_ns)
    with _report_lock:
        if _report_cache.get('key')!=key:
            full=read(path);rows=[]
            for row in full['rows']:
                m=row['metrics'];compact=dict(row)
                if m:
                    compact['metrics']={k:m[k] for k in ['current','beta_change','max_drawdown']}
                    for mode in ['fixed','rolling','twenty']:compact['metrics'][mode]={k:m[mode][k] for k in ['score','car']}
                rows.append(compact)
            _report_cache.clear();_report_cache.update(key=key,full=full,summary={**full,'rows':rows})
        return _report_cache['full']
def report():
    # GET must not create folders, publish snapshots, refresh sources or accounts.
    full=load_latest()
    with _report_lock:value=_report_cache['summary'] if _report_cache.get('full') is full else full
    return {**value,'job':job_status()}
def detail(symbol,benchmark):
    row=next((r for r in load_latest()['rows'] if r['symbol']==symbol and r['benchmark']==benchmark),None)
    if row is None:raise ValueError('找不到此商品與基準')
    return row
def latest_date():return load_latest().get('as_of')

def hac_variance(influence,lag=5):
    z=np.asarray(influence,float);value=float(z@z)
    for k in range(1,min(lag,len(z)-1)+1):value+=2*(1-k/(lag+1))*float(z[k:]@z[:-k])
    return max(0,value)
def fit_market(y,x):
    y=np.asarray(y,float);x=np.asarray(x,float);n=len(x)
    if n<30 or len(y)!=n or not np.all(np.isfinite(y)) or not np.all(np.isfinite(x)):raise ValueError('回歸樣本不足')
    xc=x-x.mean();ss=float(xc@xc)
    if ss<1e-12:raise ValueError('基準波動不足')
    beta=float(xc@(y-y.mean())/ss);alpha=float(y.mean()-beta*x.mean());e=y-alpha-beta*x
    sigma=float(np.sqrt((e@e)/(n-2)));influence=xc*e/ss
    se=math.sqrt(hac_variance(influence)*n/(n-2))
    alpha_influence=e/n-float(x.mean())*influence
    alpha_se=math.sqrt(hac_variance(alpha_influence)*n/(n-2))
    alpha_ci=[alpha-1.96*alpha_se,alpha+1.96*alpha_se]
    sst=float((y-y.mean())@(y-y.mean()))
    return dict(beta=beta,intercept=alpha,alpha_se=alpha_se,alpha_t=alpha/alpha_se if alpha_se>1e-15 else None,alpha_ci=alpha_ci,alpha_hac_lags=5,sigma=sigma,se=se,r2=1-float(e@e)/sst if sst>1e-12 else None,
                ci=[beta-1.96*se,beta+1.96*se],influence=influence)
def beta_delta(y,x):
    # The windows overlap. Put both slope influences on the common 180-day
    # timeline before estimating the variance of their difference.
    cur=fit_market(y[-120:],x[-120:]);old=fit_market(y[-180:-60],x[-180:-60])
    z=np.zeros(180);z[-120:]+=cur['influence'];z[:120]-=old['influence']
    se=math.sqrt(hac_variance(z)*120/118);delta=cur['beta']-old['beta']
    return dict(value=delta,se=se,ci=[delta-1.96*se,delta+1.96*se],direction='up' if delta>1.96*se else 'down' if delta < -1.96*se else 'flat')
def clean_fit(f):return {k:v for k,v in f.items() if k!='influence'}
def analytics(y,x,dates):
    y=np.asarray(y,float);x=np.asarray(x,float)
    if len(y)<180:raise ValueError('需至少180筆日報酬：120日估計＋60日評估')
    n=len(y);windows={}
    for days in [20,60]:
        f=fit_market(y[-days-120:-days],x[-days-120:-days]);ar=y[-days:]-f['beta']*x[-days:]
        windows[str(days)]=dict(car=float(ar.sum()*100),score=float(ar.sum()/(f['sigma']*math.sqrt(days))) if f['sigma']>=PROTOCOL['volatility_floor'] else None,
                              fit=clean_fit(f),estimate_start=dates[n-days-120],estimate_end=dates[n-days-1],start=dates[n-days],end=dates[-1],
                              half_car=float(ar[-(days//2):].sum()*100),series=np.cumsum(ar*100).tolist())
    ars=[];vars=[]
    for i in range(n-60,n):
        f=fit_market(y[i-120:i],x[i-120:i]);ars.append(float(y[i]-f['beta']*x[i]));vars.append(f['sigma']**2)
    sigma=math.sqrt(sum(vars));rolling=dict(car=sum(ars)*100,score=sum(ars)/sigma if min(vars)>=PROTOCOL['volatility_floor']**2 else None,
        half_car=sum(ars[-30:])*100,series=np.cumsum(np.array(ars)*100).tolist())
    current=clean_fit(fit_market(y[-120:],x[-120:]));current.update(estimate_start=dates[-120],estimate_end=dates[-1])
    wealth=np.r_[1,np.cumprod(1+y[-60:])];dd=float(np.min(wealth/np.maximum.accumulate(wealth)-1)*100)
    weekly=None
    # Non-overlapping complete ISO weeks, never a partly observed week.
    groups=defaultdict(list)
    for i,d in enumerate(dates):groups[datetime.fromisoformat(d).isocalendar()[:2]].append(i)
    keys=sorted(groups)
    if len(keys)>27:
        yy=[];xx=[]
        for key in keys[-28:-1]:
            ids=groups[key];yy.append(float(np.prod(1+y[ids])-1));xx.append(float(np.prod(1+x[ids])-1))
        # fit_market requires 30 for daily estimates; weekly uses a separate OLS.
        if len(yy)>=26 and np.var(xx)>1e-12:weekly=float(np.cov(yy,xx,ddof=1)[0,1]/np.var(xx,ddof=1))
    fixed=windows['60'];ratio=float(np.std(y[-20:]-current['beta']*x[-20:],ddof=1)/fixed['fit']['sigma']) if fixed['fit']['sigma']>0 else None
    return dict(fixed=fixed,twenty=windows['20'],rolling=rolling,current=current,beta_change=beta_delta(y,x),weekly_beta=weekly,
                max_drawdown=dd,return60=float((np.prod(1+y[-60:])-1)*100),return20=float((np.prod(1+y[-20:])-1)*100),
                volatility_ratio=ratio,jump_share=float(np.max(np.abs(y[-60:]-fixed['fit']['beta']*x[-60:]))/(np.sum(np.abs(y[-60:]-fixed['fit']['beta']*x[-60:]))+1e-12)),
                chart_dates=dates[-60:])

def total_returns(prices,dates,events):
    out=[]
    for prior,day in zip(dates,dates[1:]):
        p=prices.get(prior);q=prices.get(day)
        if not p or not q or p['close']<=0 or q['close']<=0:raise ValueError('交易日缺價／停牌，不向前補值')
        event=events.get(day)
        cash=0.
        if event:
            if event['unsafe']:raise ValueError('除權／增資事件尚未完成含息還原核對')
            if event.get('pre_close') is not None and abs(event['pre_close']-p['close'])>max(.011,p['close']*.0001):raise ValueError('除息前價與行情不符，待核對')
            cash=event['cash']
        r=(q['close']+cash)/p['close']-1
        if abs(r)>.32:raise ValueError('單日價格跳動超過32%，待公司事件核對')
        out.append(r)
    return out

def foreign_levels(inputs,ticker,dates):
    raw=inputs['proxies'].get(ticker,{});fx=inputs['proxies'].get('TWD=X',{});available=sorted(set(raw)&set(fx));levels={};source_days={}
    # Taiwan d uses the latest completed US session strictly before d. Holidays
    # use level ratios across the entire elapsed interval, not one shifted row.
    for d in dates:
        prior=next((v for v in reversed(available) if v<d),None)
        if not prior or (datetime.fromisoformat(d)-datetime.fromisoformat(prior)).days>6:continue
        levels[d]=raw[prior]*fx[prior];source_days[d]=prior
    return levels,source_days

def disposition_at(inputs,symbol,market,screening_date,asof):
    records=[dict(e) for e in inputs.get('dispositions',[]) if e['symbol']==symbol and e.get('published','9999')<=screening_date]
    valid=[];invalid=False
    for record in records:
        if not record.get('start') or not record.get('end'):
            dd=src.period_dates(record.get('period',''))
            if len(dd)!=2:invalid=True;continue
            record.update(start=dd[0],end=dd[1])
        valid.append(record)
    active=any(e['start']<=screening_date<=e['end'] for e in valid)
    upcoming=any(screening_date<e['start'] for e in valid)
    active_asof=any(e['published']<=asof and e['start']<=asof<=e['end'] for e in valid)
    known=bool(inputs.get('disposition_coverage',{}).get(market)) and not invalid
    state='active' if active else 'unknown' if not known else 'upcoming' if upcoming else 'ended' if valid else 'none'
    excluded=active or not known
    return dict(state=state,active=active,active_on_price_date=active_asof,upcoming=upcoming,
        excluded_by_default=excluded,exclusion_reason='發布日處置中' if active else '處置資料待核對' if not known else '',
        screening_date=screening_date,price_date=asof,observed_at=inputs.get('disposition_observed_at'),coverage_verified=known,
        announcements=valid)

def view_key(group,benchmark,market,include_disposed=False):
    return '|'.join([group,benchmark,market,'include' if include_disposed else 'exclude'])

def rank_views(result):
    views=[]
    for group,bench in sorted(set((r['group'],r['benchmark']) for r in result)):
        for market in ['all','TWSE','TPEX']:
            pool=[r for r in result if r['group']==group and r['benchmark']==bench and (market=='all' or r['market']==market)]
            if not pool:continue
            for include in [False,True]:
                eligible=[r for r in pool if r['status']=='ok' and r['metrics'] and r['metrics']['rolling']['score'] is not None and (include or not r['disposition']['excluded_by_default'])]
                ranks={};orders={}
                for mode in ['fixed','rolling']:
                    order=sorted(eligible,key=lambda r:(-r['metrics'][mode]['score'],r['symbol']));orders[mode]=order
                    ranks[mode]={r['symbol']:i+1 for i,r in enumerate(order)}
                corr=None;overlap=None;k=max(1,math.ceil(len(eligible)*.1))
                if len(eligible)>=3:
                    a=np.array([ranks['fixed'][r['symbol']] for r in eligible]);b=np.array([ranks['rolling'][r['symbol']] for r in eligible])
                    corr=float(np.corrcoef(a,b)[0,1]);overlap=len(set(r['symbol'] for r in orders['fixed'][:k])&set(r['symbol'] for r in orders['rolling'][:k]))/k*100
                alpha=[r['metrics']['current'] for r in eligible if r['metrics']['current'].get('alpha_ci') is not None]
                nonzero=sum(f['alpha_ci'][0]>0 or f['alpha_ci'][1]<0 for f in alpha)
                views.append(dict(key=view_key(group,bench,market,include),group=group,benchmark=bench,market=market,include_disposed=include,
                    count=len(eligible),total=len(pool),excluded_disposition=sum(r['disposition']['excluded_by_default'] for r in pool),
                    continuation_count={mode:sum(r['continuation' if mode=='fixed' else 'rolling_continuation'] for r in eligible) for mode in ['fixed','rolling']},
                    rank_correlation=corr,top_overlap=overlap,top_count=k if eligible else 0,ranks=ranks,
                    alpha_summary=dict(count=len(alpha),nonzero=nonzero,positive=sum(f['alpha_ci'][0]>0 for f in alpha),negative=sum(f['alpha_ci'][1]<0 for f in alpha),
                                       all_null_expected=.05*len(alpha),nominal_coverage=.95,hac_lags=5)))
    return views

def build(inputs):
    dates=inputs['dates'][-181:];by=defaultdict(dict);events=defaultdict(dict);unsafe=defaultdict(list)
    for p in inputs['prices']:by[p['symbol']][p['date']]=p
    for e in inputs['events']:events[e['symbol']][e['date']]=e
    for e in inputs['unsafe']:unsafe[e['symbol']].append(e)
    amounts={(r['date'],r['market']):r['values'] for r in inputs['amounts']}
    result=[];asof=inputs['as_of'];foreign_cache={};generated_at=src.stamp()
    for symbol,pp in sorted(by.items()):
        if asof not in pp:continue
        p=pp[asof];meta=inputs['metadata'].get(symbol,{})
        group,reason=src.classify(symbol,meta)
        if group=='other':continue
        bench_keys=['TAIEX_TR','TPEX_TR'] if group in ['stocks','domestic','leveraged'] else [src.foreign_benchmark(meta)] if group in ['overseas','overseas_leveraged'] else [None]
        for bench in bench_keys:
            row=dict(symbol=symbol,name=p['name'],market=p['market'],group=group,benchmark=bench or 'UNMATCHED',close=p['close'],as_of=asof,flags=[],status='ok',reason='',metadata_index=meta.get('標的指數/追蹤指數名稱'),
                     avg_amount=None,metrics=None,continuation=False,rolling_continuation=False)
            vals=[amounts.get((d,p['market']),{}).get(symbol) for d in dates[-20:]]
            if all(v is not None for v in vals):row['avg_amount']=sum(vals)/20
            row['disposition']=disposition_at(inputs,symbol,p['market'],generated_at[:10],asof)
            dis=row['disposition']
            if dis['active']:row['flags'].append('發布日處置中；預設排除')
            elif dis['state']=='unknown':row['flags'].append('處置狀態待核對；預設排除')
            elif dis['upcoming']:row['flags'].append('已公告即將處置；請看起訖日期')
            if group.endswith('leveraged'):row['flags'].append('槓桿／反向ETF：β為短期估計，非長期倍數')
            if bench in ['SPY','QQQ','DIA','SOXX']:row['flags'].append('含息ETF代理基準；美股前一已收盤日＋台幣換算')
            try:
                if reason:raise ValueError(reason)
                if not bench:raise ValueError('此商品的含息基準待核對；保留資料但不計CAR')
                bad=[e for e in unsafe[symbol] if dates[0]<e['date']<=asof]
                if bad:raise ValueError('期間含減資／面額變更／分割，待還原核對：'+bad[-1]['date'])
                y=total_returns(pp,dates,events[symbol])
                if bench in inputs['benchmarks']:levels=inputs['benchmarks'][bench]
                else:
                    if bench not in foreign_cache:foreign_cache[bench]=foreign_levels(inputs,bench,dates)
                    levels,source_days=foreign_cache[bench];row['foreign_source_day']=source_days.get(asof)
                if any(d not in levels for d in dates):raise ValueError('含息基準／匯率有缺日，無法計算')
                x=[levels[b]/levels[a]-1 for a,b in zip(dates,dates[1:])]
                row['metrics']=analytics(y,x,dates[1:]);m=row['metrics']
                row['continuation']=m['fixed']['car']>0 and m['twenty']['car']>0 and m['fixed']['half_car']>0
                row['rolling_continuation']=m['rolling']['car']>0 and m['twenty']['car']>0 and m['rolling']['half_car']>0
                if m['current']['r2'] is not None and m['current']['r2']<.1:row['flags'].append('市場解釋度低：請連同β區間判讀')
                if m['volatility_ratio'] and m['volatility_ratio']>2:row['flags'].append('近期殘差波動逾事前2倍')
                if m['jump_share']>.25:row['flags'].append('單日跳動貢獻集中')
                if any(.095<=abs(v)<=.105 for v in y[-20:]):row['flags'].append('近20日有接近漲跌停幅度；日β可能受價格限制影響')
                if any(pp[d].get('volume',0)==0 for d in dates[-20:] if d in pp):row['flags'].append('近20日有零成交日')
                if m['fixed']['score'] is None:raise ValueError('事前殘差波動過低，標準化分數不列排行')
                if row['avg_amount'] is None:raise ValueError('20日實際成交金額不足')
                if row['avg_amount']<PROTOCOL['liquidity_twd']:row.update(status='illiquid',reason='20日平均成交值低於2,000萬元')
            except (ValueError,KeyError,ZeroDivisionError) as e:row.update(status='unscored',reason=str(e))
            result.append(row)
    # Ranks are recomputed per benchmark, market and disposition policy.
    comparison=[]
    return dict(version=VERSION,as_of=asof,price_start=dates[0],generated_at=generated_at,rows=result,comparison=comparison,views=rank_views(result),quality=inputs['quality'],protocol=PROTOCOL,
                benchmark_names=BENCHMARKS,method='先前120日固定β及殘差波動；評估60日。AR=個股含息報酬−β×含息基準報酬，不扣歷史截距。',
                limitations=['目前價格歷史自2026年起；沒有完整下市股票池與歷史版本還原資料，不能宣稱無存活者偏差的長期回測。',
                '標準化分數是描述性強度，沒有處理所有序列相關與估計誤差，不能當顯著性、勝率或獲利保證。',
                '股票採除息日現金股利總報酬；除權、減資及分割尚未逐筆還原者不列入排行。',
                '處置狀態依本次發布日及已取得公告判斷；未知狀態預設排除，歷史驗證不使用後來的清單回推。'])

def make_snapshot(report):
    rows=[]
    for r in report['rows']:
        if r['status']!='ok' or r['metrics']['rolling']['score'] is None:continue
        rows.append({k:r[k] for k in ['symbol','group','market','benchmark','continuation','rolling_continuation','disposition']}|dict(
            beta=r['metrics']['current']['beta'],alpha=r['metrics']['current']['intercept'],alpha_se=r['metrics']['current']['alpha_se'],
            alpha_ci=r['metrics']['current']['alpha_ci'],fixed_score=r['metrics']['fixed']['score'],rolling_score=r['metrics']['rolling']['score'],
            excluded_at_publication=r['disposition']['excluded_by_default']))
    sets=[]
    # Every benchmark/market/policy comparison is fixed now, before outcomes.
    for view in report['views']:
        if not view['count']:continue
        for mode,flag in [('fixed','continuation'),('rolling','rolling_continuation')]:
            ranks=view['ranks'][mode]
            universe=[r for r in rows if r['group']==view['group'] and r['benchmark']==view['benchmark'] and r['symbol'] in ranks and r[flag]]
            order=sorted(universe,key=lambda r:ranks[r['symbol']]);top=order[:max(1,math.ceil(len(order)*.1))]
            sets.append(dict(key=view['key']+'|'+mode,group=view['group'],benchmark=view['benchmark'],market=view['market'],
                mode=mode,benchmark_modes=(['matched','common'] if (view['benchmark'],view['market']) in [('TAIEX_TR','TWSE'),('TPEX_TR','TPEX')] else ['common'] if view['benchmark'] in ['TAIEX_TR','TPEX_TR'] else ['foreign']),include_disposed=view['include_disposed'],disposition_policy='include' if view['include_disposed'] else 'exclude_active_and_unknown',
                candidates=[r['symbol'] for r in order],selected=[r['symbol'] for r in top],ranks={r['symbol']:ranks[r['symbol']] for r in order}))
    return dict(version=VERSION,method_version=VERSION,published_at=report['generated_at'],as_of=report['as_of'],protocol=PROTOCOL,rows=rows,ranking_sets=sets)

def validation(inputs,snapshots):
    """Use frozen membership and exclusions, never today's disposition list."""
    dates=inputs['dates'];out=[];by=defaultdict(dict);events=defaultdict(dict)
    for p in inputs['prices']:by[p['symbol']][p['date']]=p
    for e in inputs['events']:events[e['symbol']][e['date']]=e
    for snap in snapshots:
        publication=snap['published_at'][:10]
        # A rolling input history may no longer contain the actual first
        # post-publication session. Keep prior outcomes; never shift the start.
        if not dates or publication<dates[0]:continue
        future=[d for d in dates if d>publication]
        if not future:continue
        lookup={(r['symbol'],r['benchmark']):r for r in snap['rows']}
        for days in [20,60]:
            if len(future)<=days:continue
            window=future[:days+1]
            for ranking in snap['ranking_sets']:
                group=ranking['group'];bench=ranking['benchmark'];selected=set(ranking['selected']);scored=[];missing=0
                if bench not in inputs['benchmarks']:levels,_=foreign_levels(inputs,bench,window)
                else:levels=inputs['benchmarks'][bench]
                if any(d not in levels for d in window):continue
                xr=np.array([levels[b]/levels[a]-1 for a,b in zip(window,window[1:])])
                for symbol in ranking['candidates']:
                    r=lookup[symbol,bench]
                    try:
                        if any(e['symbol']==symbol and window[0]<e['date']<=window[-1] for e in inputs['unsafe']):raise ValueError('company event')
                        y=np.array(total_returns(by[symbol],window,events[symbol]))
                        scored.append((symbol,float(np.sum(y-r['beta']*xr)*100),float((np.prod(1+y)-1)*100)))
                    except ValueError:missing+=1
                top=[v for v in scored if v[0] in selected];ic=None
                if len(scored)>=5:
                    ranks=np.array([ranking['ranks'][v[0]] for v in scored],float);rr=average_ranks([v[1] for v in scored])
                    if np.std(rr)>0:ic=float(np.corrcoef(average_ranks(-ranks),rr)[0,1])
                protocol=snap['protocol'];cost=(protocol['commission_bp_per_side']*2+protocol['slippage_bp_per_side']*2+(protocol['stock_sell_tax_bp'] if group=='stocks' else protocol['etf_sell_tax_bp']))/100
                out.append(dict(method_version=snap['version'],published_at=snap['published_at'],as_of=snap['as_of'],start=window[0],end=window[-1],days=days,
                    group=group,benchmark=bench,market=ranking['market'],mode=ranking['mode'],view_key=ranking['key'],disposition_policy=ranking['disposition_policy'],
                    rank_ic=ic,selected=len(selected),priced=len(top),missing=missing,selected_symbols=ranking['selected'],
                    mean_net_return=sum(v[2]-cost for v in top)/len(selected) if selected else None,
                    mean_forward_car=sum(v[1] for v in top)/len(selected) if selected else None,partial=len(top)<len(selected)))
    previous_data=read(src.folder()/'validation'/(VERSION+'.json'),None)
    if previous_data is None:previous_data=read(src.folder()/'validation.json',{})
    previous=previous_data.get('mature',[]) if previous_data.get('version')==VERSION else []
    key=lambda v:(v['as_of'],v['view_key'],v['days'])
    saved={key(v):v for v in previous};saved.update({key(v):v for v in out})
    return dict(version=VERSION,mature=list(saved.values()),snapshots=len(snapshots),first_published=min((s['published_at'] for s in snapshots),default=None),
        note='依方法版本、基準、市場與處置政策分開追蹤；處置排除使用首次封存的狀態，不以最新清單回推。重疊20／60日觀察不視為獨立樣本；缺價須標示部分結果。')

def _run():
    try:
        inputs=src.collect(lambda msg:state(message=msg));state(message='計算事前固定β、HAC區間及滾動版比較')
        value=build(inputs)
        if not any(r['status']=='ok' for r in value['rows']):raise ValueError('沒有可核對完整資料的商品；保留上次排行')
        root=src.folder();root.mkdir(parents=True,exist_ok=True)
        encoded=json.dumps(inputs,ensure_ascii=False,allow_nan=False).encode();value['input_sha256']=hashlib.sha256(encoded).hexdigest()
        # One immutable first publication per data date. Refreshes may correct the
        # displayed report but cannot improve or replace the prospective signal.
        snap_path=root/'snapshots'/(value['as_of']+'-'+VERSION+'.json')
        publication_ready=all(inputs.get('disposition_coverage',{}).get(m) for m in ['TWSE','TPEX'])
        if publication_ready and not snap_path.exists():
            archive=root/'inputs'/(value['input_sha256']+'.json.gz');archive.parent.mkdir(parents=True,exist_ok=True)
            if not archive.exists():
                with gzip.open(archive,'xb') as f:f.write(encoded)
            snap=make_snapshot(value);snap['input_sha256']=value['input_sha256'];publish_once(snap_path,snap)
        snapshots=[json.loads(p.read_text()) for p in sorted((root/'snapshots').glob('*.json'))]
        snapshots=[s for s in snapshots if s.get('version')==VERSION]
        value['validation']=validation(inputs,snapshots)
        value['validation']['publication_ready']=publication_ready
        if not publication_ready:value['validation']['note']+=' 本次處置來源未完整核對，暫不新增首次前瞻封存。'
        tmp=root/'inputs.json.gz.tmp'
        with gzip.open(tmp,'wb') as f:f.write(encoded)
        tmp.replace(root/'inputs.json.gz')
        previous=read(root/'validation.json',{})
        old_version=previous.get('version','')
        if old_version and old_version!=VERSION and all(c.isalnum() or c in '-._' for c in old_version):publish_once(root/'validation'/(old_version+'.json'),previous)
        atomic(root/'validation'/(VERSION+'.json'),value['validation'])
        atomic(root/'validation.json',value['validation']);atomic(root/'latest.json',value)
        state(status='complete' if publication_ready else 'partial',message=f"完成 {value['as_of']}：分組排行、α區間及處置篩選已更新（{VERSION}）"+('' if publication_ready else '；處置來源待核對，暫不封存前瞻名單'),finished_at=src.stamp())
    except Exception as e:
        traceback.print_exc();state(status='failed',message=str(e)[:500]+'；保留上次已完成資料',finished_at=src.stamp())
    finally:_lock.release()
def refresh():
    if not _lock.acquire(False):return job_status()
    state(status='running',message='正在整理含息資料，首次需數分鐘',started_at=src.stamp(),finished_at=None)
    threading.Thread(target=_run,daemon=True).start();return job_status()
