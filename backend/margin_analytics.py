"""Packages 3–5: gated financing aggregates, distributions and stock cohorts.

Source observations and model estimates stay separate. Building is explicit;
GET never calculates, downloads, refreshes accounts, or promotes a model.
"""
from pathlib import Path
from functools import lru_cache
import gzip,json,math
from . import db,margin_statistics as sources,margin_principal as principal

VERSION='margin-analytics-3-5-1'
METHODS=('net_average','net_fifo')
LABELS={'listed_stock':'上市個股','otc_stock':'上櫃個股','etf_cap':'市值型ETF','etf_dividend':'高股息ETF','etf_leverage':'正向槓桿ETF','etf_inverse':'反向ETF（另列）','other':'其他／待分類'}
BANDS=('已破130%','距130%：0～7%','距130%：7～13%','距130%：13～22%','距130%：>22%')
SUBSETS={'debt30':'融資金額前30','increase30':'20日融資增加金額前30','rs30':'20日RS前30','ytd':'YTD贏過大盤'}
EMPTY_METRICS={k:None for k in ('maintenance','buffer','loan','loan_cap','below130_amount','below130_share','below130_count','change20','index_buffer')}

def positive(x):return isinstance(x,(int,float)) and math.isfinite(x) and x>0
def classify(symbol,name,market):
    bucket=sources.classify(symbol,name,market)
    if bucket=='etf_leverage':
        if symbol.endswith('R') or '反1' in name or '反2' in name:return 'etf_inverse'
        if symbol.endswith('L') or '正2' in name:return 'etf_leverage'
        return 'other'
    return bucket

def metrics(rows,method='net_average',base20=None,market_cap=None,etf=False):
    """Internal candidate only. All active rows must be identified; no partial sums."""
    out=dict(EMPTY_METRICS);active=[r for r in rows if r['quantity']>0]
    if not active:return out
    debt=[];collateral=[]
    for r in active:
        m=r['models'][method];p=m['principal']
        if not positive(p) or m.get('unpriced_units',0)>principal.EPS or abs(m['quantity']-r['quantity'])>principal.EPS:return out
        debt.append(p)
        collateral.append(r['quantity']*1000*r['close'] if positive(r['close']) else None)
    loan=sum(debt);out['loan']=loan
    out['loan_cap']=loan/market_cap if positive(market_cap) else None
    if base20 is not None:
        b=metrics(base20,method)
        if positive(b['loan']):out['change20']=loan/b['loan']-1
    if any(x is None for x in collateral):return out
    maintenance=sum(collateral)/loan
    low=[i for i,x in enumerate(collateral) if x/debt[i]<1.30]
    out.update(maintenance=maintenance,buffer=1-1.30/maintenance,below130_amount=sum(debt[i] for i in low),below130_share=sum(debt[i] for i in low)/loan,below130_count=None if etf else len(low))
    return out

def distribution(rows,method='net_average',etf=False):
    amount=[0.]*5;count=[0]*5;active=0
    for r in rows:
        if r['quantity']<=0:continue
        active+=1;m=r['models'][method];debt=m['principal']
        if not positive(debt) or not positive(r['close']) or m.get('unpriced_units',0)>principal.EPS or abs(m['quantity']-r['quantity'])>principal.EPS:return None
        i=principal.bucket(r['quantity']*1000*r['close']/debt);amount[i]+=debt;count[i]+=1
    total=sum(amount)
    if not active or total<=0:return None
    return [{'label':label,'amount':a,'share':a/total,'count':None if etf else n} for label,a,n in zip(BANDS,amount,count)]

def compared_distribution(rows,etf=False):
    a,b=[distribution(rows,k,etf) for k in METHODS]
    if a is None or b is None:return {'tier':'unverified','D':None,'low_difference':None,'bins':None}
    d=principal.distribution_distance([r['share'] for r in a],[r['share'] for r in b]);low=abs(a[0]['share']-b[0]['share']);tier=principal.sensitivity_tier(d,low)
    bins=[]
    for x,y in zip(a,b):
        bins.append({'label':x['label'],'amount':x['amount'] if tier=='point' else None,'share':x['share'] if tier=='point' else None,'count':x['count'] if tier=='point' else None,'amount_range':[min(x['amount'],y['amount']),max(x['amount'],y['amount'])],'share_range':[min(x['share'],y['share']),max(x['share'],y['share'])],'count_range':None if etf else [min(x['count'],y['count']),max(x['count'],y['count'])]})
    return {'tier':tier,'D':d,'low_difference':low,'bins':bins if tier in ('point','interval') else None}

def gate_reasons(grade):
    if not grade or grade.get('source_missing'):return ['本日缺融資來源']
    reasons=[]
    if grade.get('accepted') is not True:reasons.append('包2尚未通過採用驗收')
    names={'total':'完整本金總量±3%','warmup':'每檔未知存量≤10%','drift':'60日漂移≤1個百分點','price_identified':'價格與完整本金識別','cap_rule_verified':'歷史融資成數核對','disclosure_continuity':'揭露連續性'}
    for key,label in names.items():
        if not all(grade.get('quantitative_gates',{}).get(method,{}).get(key) is True for method in METHODS):reasons.append(label+'未通過／未驗證')
    if grade.get('raw_sensitivity_tier') not in ('point','interval'):reasons.append('完整金額分布敏感度未通過／未驗證')
    if grade.get('publication_blockers'):reasons.append('公司行動、歷史成數與來源版本資格尚待核對')
    return list(dict.fromkeys(reasons))

def source_observations(rows,universe,complete):
    """Do not call financed market value 'loan'. Cap includes unfinanced names."""
    active=[r for r in rows if r['quantity']>0]
    valid=[r for r in active if positive(r['close'])]
    cap_valid=[r for r in universe if positive(r.get('shares')) and positive(r['close'])]
    cap_complete=bool(universe) and len(cap_valid)==len(universe)
    return {'source_complete':complete,'source_names':len(rows),'financed_names':len(active) if complete else None,
        'reported_financed_names':len(active),'finance_units':sum(r['quantity'] for r in active) if complete else None,
        'reported_finance_units':sum(r['quantity'] for r in active),'financed_market_value':sum(r['quantity']*1000*r['close'] for r in valid) if complete and len(valid)==len(active) else None,
        'priced_names':len(valid),'universe_names':len(universe),'cap_covered_names':len(cap_valid),
        'market_cap':sum(r['shares']*r['close'] for r in cap_valid) if cap_complete else None,'cap_complete':cap_complete,
        'classification_verified':False}

def select_subsets(rows,base20,rs_snapshot,day,market):
    stocks=[r for r in rows if r['quantity']>0 and classify(r['symbol'],r['name'],market) in ('listed_stock','otc_stock')]
    by={r['symbol']:r for r in stocks}
    def valid(r):
        m=r['models']['net_average'];p=m['principal']
        return p is not None and math.isfinite(p) and (p>0 or p==0 and r['quantity']==0) and m.get('unpriced_units',0)<=principal.EPS
    ordered=sorted([r for r in stocks if valid(r)],key=lambda r:(-r['models']['net_average']['principal'],r['symbol']))
    debt=[{'symbol':r['symbol'],'name':r['name'],'rank_value':r['models']['net_average']['principal']} for r in ordered[:30]]
    increases=[]
    if base20 is not None:
        old={r['symbol']:r for r in base20}
        for r in stocks:
            b=old.get(r['symbol'])
            if valid(r) and b and valid(b):
                delta=r['models']['net_average']['principal']-b['models']['net_average']['principal']
                if delta>0:increases.append({'symbol':r['symbol'],'name':r['name'],'rank_value':delta})
    increases=sorted(increases,key=lambda r:(-r['rank_value'],r['symbol']))[:30]
    result={'debt30':debt,'increase30':increases,'rs30':None,'ytd':None}
    if rs_snapshot and rs_snapshot.get('as_of')==day:
        def eligible(r):return r.get('market')==market and r.get('symbol') in by
        if '20' in rs_snapshot.get('groups',{}):result['rs30']=[{'symbol':r['symbol'],'name':by[r['symbol']]['name'],'rank_value':r['rs']} for r in rs_snapshot['groups']['20'] if eligible(r)][:30]
        if 'ytd' in rs_snapshot.get('groups',{}):result['ytd']=[{'symbol':r['symbol'],'name':by[r['symbol']]['name'],'rank_value':r['excess_pct']} for r in rs_snapshot['groups']['ytd'] if eligible(r) and r.get('excess_pct',0)>0]
    return result

def cohort_metrics(rows,members,whole,prior5=None):
    by={r['symbol']:r for r in rows};symbols=[r['symbol'] for r in members]
    empty={'maintenance':None,'market_difference':None,'below130_amount':None,'change5_pp':None}
    if not symbols or any(s not in by for s in symbols):return empty
    current=metrics([by[s] for s in symbols]);market=metrics(whole)
    out={'maintenance':current['maintenance'],'market_difference':current['maintenance']-market['maintenance'] if current['maintenance'] is not None and market['maintenance'] is not None else None,'below130_amount':current['below130_amount'],'change5_pp':None}
    if prior5 is not None:
        past={r['symbol']:r for r in prior5}
        if all(s in past for s in symbols):
            old=metrics([past[s] for s in symbols])
            if old['maintenance'] is not None and current['maintenance'] is not None:out['change5_pp']=(current['maintenance']-old['maintenance'])*100
    return out

def public_basket(key,rows,universe,grades,base20=None,base_grades=None,events=()):
    etf=key.startswith('etf_');reasons=[]
    for market,grade in grades.items():reasons += [market+'：'+r for r in gate_reasons(grade)]
    if etf or key=='other':reasons.append('ETF／商品歷史分類、除息及倍率尚未逐檔驗證')
    complete=all(g and not g.get('source_missing') for g in grades.values());observed=source_observations(rows,universe,complete)
    if not observed['cap_complete']:reasons.append('該籃總市值缺同日股數或收盤，不使用局部市值當分母')
    sensitivity=compared_distribution(rows,etf)
    if sensitivity['tier'] not in ('point','interval'):reasons.append('該籃兩方法分布敏感度未通過／未驗證')
    reasons=list(dict.fromkeys(reasons));ready=not reasons
    candidate=metrics(rows,base20=base20 if base_grades and all(not gate_reasons(g) for g in base_grades.values()) else None,market_cap=observed['market_cap'],etf=etf)
    # A missing denominator does not hide its cause, and never leaks provisional ratios.
    return {'key':key,'label':LABELS[key],'etf':etf,'status':'point' if ready and sensitivity['tier']=='point' else 'interval' if ready else 'blocked',
        'reasons':reasons,'observed':observed,'metrics':candidate if ready and sensitivity['tier']=='point' else dict(EMPTY_METRICS),
        'distribution':sensitivity if ready else {'tier':'blocked','D':None,'low_difference':None,'bins':None},
        'events':[{'symbol':e['symbol'],'kind':e['kind'],'cash':e.get('cash'),'date':e['date']} for e in events if any(r['symbol']==e['symbol'] and r['market']==e['market'] for r in universe)],
        'index_buffer_note':'僅經逐商品驗證的正向2倍ETF，才可另估緩衝÷2；目前不推算' if key=='etf_leverage' else None}

def public_subsets(market,day,rows,base20,prior5,grade,base_grade,prior_grade,rs_snapshot):
    selected=select_subsets(rows,base20,rs_snapshot,day,market);reasons=gate_reasons(grade)
    source_available=bool(grade and not grade.get('source_missing'))
    stocks=[r for r in rows if classify(r['symbol'],r['name'],market) in ('listed_stock','otc_stock')]
    out=[]
    for key,label in SUBSETS.items():
        why=list(reasons);members=selected[key];observable=key in ('rs30','ytd')
        if key=='increase30' and (base20 is None or gate_reasons(base_grade)):why.append('缺少通過驗收的20交易日前本金')
        if members is None:why.append('缺同日RS封存；不套用最近一天名單')
        if observable and not source_available:why.append('缺本日融資來源，無法確認RS名單的有融資交集')
        publish_members=members if (observable and source_available) or not why else None
        ready=not why
        values=cohort_metrics(stocks,members or [],stocks,prior5 if not gate_reasons(prior_grade) else None) if ready else {k:None for k in ('maintenance','market_difference','below130_amount','change5_pp')}
        out.append({'key':key,'label':label,'status':'ready' if ready else 'blocked','reasons':list(dict.fromkeys(why)),'members':publish_members,'count':len(publish_members) if publish_members is not None else None,'metrics':values,'membership_source':'同日既有RS封存，與有融資普通股交集' if observable else '融資本金排名，待完整市場驗收','rs_date':rs_snapshot.get('as_of') if rs_snapshot and observable else None,'change5_definition':'本日成員固定回看5交易日的維持率差（百分點）；不是前瞻預測'})
    return out

@lru_cache(maxsize=2)
def _load(path,mtime,size):return json.loads(gzip.decompress(Path(path).read_bytes()))
def report(day=None,*,store=None):
    path=(Path(store) if store else sources.folder())/'analytics.json.gz'
    if not path.exists():return {'version':VERSION,'status':'missing','model_ready':False,'dates':[],'frame':None}
    stat=path.stat();data=_load(str(path),stat.st_mtime_ns,stat.st_size)
    if data.get('version')!=VERSION or data.get('model_ready') is not False:raise ValueError('包3～5封存版本或驗收狀態不符')
    chosen=day or data['dates'][-1]
    if chosen not in data['dates']:raise ValueError('該日不在封存交易日曆內')
    # Defense at the serving boundary: this version has no authority to promote Package2.
    frame=data['frames'][chosen]
    if any(b['status']!='blocked' or any(v is not None for v in b['metrics'].values()) or b['distribution']['bins'] is not None for b in frame['baskets']):raise ValueError('未通過的維持率或分布不可發布')
    for groups in frame['subsets'].values():
        if any(s['status']!='blocked' or any(v is not None for v in s['metrics'].values()) or s['key'] in ('debt30','increase30') and s['members'] is not None for s in groups):raise ValueError('未通過的子集本金或排名不可發布')
    return {k:v for k,v in data.items() if k!='frames'}|{'frame':frame,'as_of':chosen}
