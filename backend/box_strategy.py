"""Deterministic box-pullback screening frozen into each research report."""
from __future__ import annotations
import json,math,re
from datetime import date,datetime
from statistics import mean
import requests
from . import research,strategies

VERSION='box-pullback-v1'
METHOD=('還原日線、N=3 已確認擺動點；箱高 3%–25%、上下緣各至少兩次觸及、'
        '區間內收盤率至少 80%、箱期至少 8 日且未收破箱底。趨勢須收盤>MA60>MA120、'
        'MA60 斜率為正，RS60≥70、RS120≥60、接近 250 日高點、無近期低點下移、'
        '族群近5日法人為正、20日均成交額至少1.5億元。收盤進入箱底30%且量縮並出現止跌。'
        '計算結果只作為研究報告的箱底參考；未靠近箱底或未通過完整箱型，不排除原本的研究候選。')

def avg(values):return mean(values) if values else None
def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def swings(rows,n=3):
    highs=[];lows=[]
    for i in range(n,len(rows)-n):
        window=rows[i-n:i+n+1];hi=rows[i]['adj_high'];lo=rows[i]['adj_low']
        if hi==max(r['adj_high'] for r in window) and sum(abs(r['adj_high']-hi)<1e-9 for r in window)==1:highs.append(i)
        if lo==min(r['adj_low'] for r in window) and sum(abs(r['adj_low']-lo)<1e-9 for r in window)==1:lows.append(i)
    return highs,lows

def atr20(rows):
    if len(rows)<21:return None
    tr=[]
    for i in range(len(rows)-20,len(rows)):
        prev=rows[i-1]['adj'];r=rows[i]
        tr.append(max(r['adj_high']-r['adj_low'],abs(r['adj_high']-prev),abs(r['adj_low']-prev)))
    return avg(tr)

def market_percentiles(target,periods=(60,120)):
    """Market-wide raw-price reference; candidate returns remain adjusted."""
    with research.main_db() as c:
        dates=[r[0] for r in c.execute('SELECT DISTINCT date FROM prices WHERE date<=? ORDER BY date DESC LIMIT 260',(target,))]
        result={}
        for period in periods:
            if len(dates)<=period:result[period]=[];continue
            anchor=dates[period]
            rows=c.execute('''SELECT a.symbol,a.close/b.close-1 FROM prices a JOIN prices b USING(symbol)
                              WHERE a.date=? AND b.date=? AND a.close>0 AND b.close>0''',(target,anchor)).fetchall()
            result[period]=sorted(float(r[1]) for r in rows if re.fullmatch(r'\d{4}',r[0]) and not r[0].startswith('00') and finite(r[1]))
    return result

def percentile(values,value):
    if not values:return None
    below=sum(v<value for v in values);equal=sum(abs(v-value)<1e-12 for v in values)
    return round(100*(below+(equal-1)/2)/max(1,len(values)-1),2)

def adjusted_return(rows,period):
    return rows[-1]['adj']/rows[-period-1]['adj']-1 if len(rows)>period else None

def roc_day(value):
    parts=re.findall(r'\d+',str(value));digits=''.join(parts)
    if len(digits)==7:return date(int(digits[:3])+1911,int(digits[3:5]),int(digits[5:])).isoformat()
    if len(parts)>=3:return date(int(parts[0])+1911 if int(parts[0])<1911 else int(parts[0]),int(parts[1]),int(parts[2])).isoformat()
    raise ValueError('民國日期格式改變')

def official_restrictions(target):
    if target!=datetime.now(research.TZ).date().isoformat():return set(),False,['歷史注意／處置名單不以目前清單回填，F7 保守視為未核實']
    sources=[
        ('https://openapi.twse.com.tw/v1/announcement/notice','Code','Date',None),
        ('https://openapi.twse.com.tw/v1/announcement/punish','Code','Date','DispositionPeriod'),
        ('https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information','SecuritiesCompanyCode','Date',None),
        ('https://www.tpex.org.tw/openapi/v1/tpex_disposal_information','SecuritiesCompanyCode','Date','DispositionPeriod')]
    blocked=set();warnings=[]
    for url,code_field,date_field,period_field in sources:
        try:
            response=requests.get(url,headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,25),allow_redirects=False);response.raise_for_status();rows=response.json()
            if not isinstance(rows,list):raise ValueError
            for row in rows:
                code=str(row.get(code_field,'')).strip()
                if not re.fullmatch(r'\d{4}',code):continue
                if period_field:
                    dates=re.findall(r'\d{3,4}[/]?\d{2}[/]?\d{2}',str(row.get(period_field,'')))
                    if len(dates)>=2 and roc_day(dates[0])<=target<=roc_day(dates[1]):blocked.add(code)
                elif row.get(date_field) and roc_day(row[date_field])==target:blocked.add(code)
        except (requests.RequestException,ValueError,TypeError):warnings.append('官方注意／處置名單讀取失敗：'+url)
    return blocked,not warnings,warnings

def inspect(candidate,quote,target,distributions,restricted=None,restriction_verified=True):
    rows=[r for r in quote.get('rows',[]) if r['date']<=target and all(finite(r.get(k)) for k in ['adj','adj_open','adj_high','adj_low','volume'])]
    checks={};fail=[]
    if len(rows)<140 or rows[-1]['date']!=target:
        return {'symbol':candidate['symbol'],'name':candidate.get('name',candidate['symbol']),'signal':False,'failures':['還原日線不足140筆或研究日行情缺少'],'checks':checks}
    hs,ls=swings(rows);high_i=hs[-1] if hs else None;lows_after=[i for i in ls if high_i is not None and i>high_i]
    if high_i is None or not lows_after:
        return {'symbol':candidate['symbol'],'name':candidate.get('name',candidate['symbol']),'signal':False,'failures':['找不到已確認箱頂與其後箱底'],'checks':checks}
    low_i=min(lows_after,key=lambda i:rows[i]['adj_low']);bh=rows[high_i]['adj_high'];bl=rows[low_i]['adj_low'];height=(bh-bl)/bl
    segment=rows[high_i:];duration=len(segment)-1;band=.015
    top=sum(r['adj_high']>=bh*(1-band) and r['adj_low']<=bh*(1+band) for r in segment)
    bottom=sum(r['adj_low']<=bl*(1+band) and r['adj_high']>=bl*(1-band) for r in segment)
    coverage=sum(bl<=r['adj']<=bh for r in segment)/len(segment)
    box_rules={'V1':.03<=height<=.25,'V2':top>=2 and bottom>=2,'V3':coverage>=.8,'V4':duration>=8,'V5':all(r['adj']>=bl for r in segment)}
    checks.update(box_rules)
    ma60=avg([r['adj'] for r in rows[-60:]]);ma120=avg([r['adj'] for r in rows[-120:]])
    old60=avg([r['adj'] for r in rows[-80:-20]]) if len(rows)>=140 else None
    ret60=adjusted_return(rows,60);ret120=adjusted_return(rows,120)
    rs60=percentile(distributions.get(60,[]),ret60) if ret60 is not None else None
    rs120=percentile(distributions.get(120,[]),ret120) if ret120 is not None else None
    recent_lows=[i for i in ls if i>=len(rows)-60]
    no_lower_low=len(recent_lows)<2 or rows[recent_lows[-1]]['adj_low']>=rows[recent_lows[-2]]['adj_low']
    high250=max(r['adj_high'] for r in rows[-250:])
    turnover=avg([r['close']*r['volume'] for r in rows[-20:]])
    last=rows[-1];f_rules={
        'F1':last['adj']>ma60>ma120 and ma60>(old60 or ma60),
        'F2':rs60 is not None and rs120 is not None and rs60>=70 and rs120>=60,
        'F3':last['adj']>=high250*.75,
        'F4':no_lower_low,
        'F5':bool(candidate.get('sector_hits')),
        'F6':turnover is not None and turnover>=150_000_000,
        'F7':restriction_verified and candidate['symbol'] not in (restricted or set()) and not any(r['adj']==r['adj_high'] and i and r['adj']/rows[i-1]['adj']>=1.09 for i,r in enumerate(rows[-5:],start=len(rows)-5)),
    };checks.update(f_rules)
    box_volume=avg([r['volume'] for r in segment]);vol5=avg([r['volume'] for r in rows[-5:]])
    zone=bl<=last['adj']<=bl+(bh-bl)*.30
    upper_half=last['adj']>last['adj_open'] and last['adj']>=(last['adj_high']+last['adj_low'])/2
    reclaim=last['adj_low']<bl and last['adj']>bl
    no_new_low=len(rows)>=3 and rows[-1]['adj_low']>=min(rows[-2]['adj_low'],rows[-3]['adj_low'])
    e_rules={'E1':zone,'E2':vol5 is not None and box_volume is not None and vol5<=box_volume*.75,'E3':last['adj']>bl,'E4':upper_half or reclaim or no_new_low};checks.update(e_rules)
    labels={'V1':'箱高不在3%–25%','V2':'上下緣觸及不足2次','V3':'箱內收盤率低於80%','V4':'箱期不足8日','V5':'曾收破箱底','F1':'MA60/120趨勢未通過','F2':'RS60/RS120未達70/60','F3':'低於250日高點75%','F4':'近60日低點仍下移','F5':'所屬族群近5日法人未轉正','F6':'20日均成交額低於1.5億元','F7':'官方注意／處置或近5日鎖漲停檢查未通過','E1':'收盤未進入箱底30%區','E2':'5日均量未縮至箱內75%','E3':'收盤未守箱底','E4':'未出現規格書止跌條件'}
    fail=[labels[k] for k,v in checks.items() if not v]
    atr=atr20(rows);stop=bl-.3*atr if atr else None;tp1=bh+(bh-bl)
    return {'symbol':candidate['symbol'],'name':candidate.get('name',candidate['symbol']),'signal':not fail,'failures':fail,'checks':checks,
            'close':round(last['adj'],4),'box_top':round(bh,4),'box_bottom':round(bl,4),'box_height_pct':round(height*100,2),'box_days':duration,
            'top_touches':top,'bottom_touches':bottom,'coverage_pct':round(coverage*100,1),'box_start':rows[high_i]['date'],'box_low_date':rows[low_i]['date'],
            'rs60':rs60,'rs120':rs120,'turnover20':round(turnover,2) if turnover else None,'atr20':round(atr,4) if atr else None,
            'take_profit':round(tp1,2),'stop_loss':round(stop,2) if stop and stop>0 else None,'probe_size_pct':30 if duration>90 else 60,
            'stale':duration>90,'entry':'下一交易日開盤','cost_rate_pct':.87,
            'rule':{'entry':'next_open','probe_size_pct':30 if duration>90 else 60,'add_size_pct':20 if duration>90 else 40,'box_top':bh,'box_bottom':bl,'atr20':atr,'initial_stop':stop,'tp1':tp1,'tp1_sell_pct':50,'time_stop_days':15,'circuit_breaker_pct':min(height*1.5,.08)*100,'round_trip_cost_pct':.87,'version':VERSION}}

def prepare(evidence):
    target=evidence['date'];candidates=evidence.get('candidates',[]);warnings=[]
    distributions=market_percentiles(target);restricted,restriction_verified,restriction_warnings=official_restrictions(target);warnings.extend(restriction_warnings)
    with research.store() as c:
        strategies.init(c);cached={r['symbol']:json.loads(r['payload']) for r in c.execute('SELECT * FROM strategy_quotes')}
    inspected=[]
    for candidate in candidates:
        symbol=candidate['symbol'];quote=cached.get(symbol)
        complete=quote and any(r.get('date')==target and all(k in r for k in ['adj_high','adj_low','volume']) for r in quote.get('rows',[]))
        if not complete:
            try:
                market=candidate.get('market')
                if not market:
                    with research.main_db() as c:
                        row=c.execute('SELECT market FROM prices WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1',(symbol,target)).fetchone();market=row[0] if row else 'TWSE'
                quote=strategies.fetch_chart(symbol,market,target)
                with research.store() as c:strategies.init(c);c.execute('INSERT OR REPLACE INTO strategy_quotes VALUES (?,?)',(symbol,json.dumps(quote,ensure_ascii=False)))
            except ValueError as error:
                inspected.append({'symbol':symbol,'name':candidate.get('name',symbol),'signal':False,'failures':['行情取得失敗：'+str(error)],'checks':{}});continue
        inspected.append(inspect(candidate,quote,target,distributions,restricted,restriction_verified))
    signals=[];group_counts={}
    for row in sorted((r for r in inspected if r['signal']),key=lambda r:(r['stale'],-(r['rs60'] or 0),r['symbol'])):
        candidate=next(c for c in candidates if c['symbol']==row['symbol']);groups=candidate.get('sector_hits') or ['未分類'];group=groups[0]
        if group_counts.get(group,0)>=2:
            row['signal']=False;row['failures']=['同族群最多2檔'];continue
        group_counts[group]=group_counts.get(group,0)+1;signals.append(row)
    near=sorted((r for r in inspected if not r['signal']),key=lambda r:(len(r['failures']),-(r.get('rs60') or 0),r['symbol']))[:10]
    if candidates:warnings.append('F7 已核對報告日證交所／櫃買注意與處置名單及近5日疑似鎖漲停；公司別財報公告日前3交易日仍需人工覆核。')
    levels=[{k:r.get(k) for k in ['symbol','name','box_top','box_bottom','signal','failures']} for r in inspected]
    return {'date':target,'version':VERSION,'method':METHOD,'signals':signals,'near_misses':near,
            'levels':levels,'tested':len(inspected),'warnings':warnings,
            'source':'Yahoo 還原日線＋本機市場、RS、族群快照'}
