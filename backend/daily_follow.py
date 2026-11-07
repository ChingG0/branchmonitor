"""Frozen daily branch-follow signals and no-lookahead directional backtests."""
from __future__ import annotations

import json
import math
import random
import re
import sqlite3
from collections import Counter,defaultdict,deque
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo

import requests

from . import broker_focus, db, rs, sector_flow, holding_duration

TZ = ZoneInfo('Asia/Taipei')
HISTORY_START = '2026-05-01'
TPEX_INDEX_START = (date.fromisoformat(HISTORY_START)-timedelta(days=45)).isoformat()
TPEX_INDEX_URL = 'https://www.tpex.org.tw/www/zh-tw/indexInfo/inx'
VERSION = 'daily-follow-v3'
V4_VERSION = 'daily-follow-v4'
V4_HOLD = 20
V4_WAIT = 5
V4_PULLBACK = .05
V4_SECTOR_WEIGHT = .08
V1_TAKE_PROFIT = .08
V1_STOP_LOSS = .08
V5_ENTRY_BIAS = 0.
V5_EXIT_BIAS = 6.
BUY_FRICTION = .002 + .001425 * .6
SELL_FRICTION = .002 + .001425 * .6 + .003
ROUND_TRIP_PCT = ((1 + BUY_FRICTION) / (1 - SELL_FRICTION) - 1) * 100
ETF_SELL_FRICTION = .002 + .001425 * .6 + .001
ETF_ROUND_TRIP_PCT = ((1 + BUY_FRICTION) / (1 - ETF_SELL_FRICTION) - 1) * 100
BACKTEST_HORIZONS = (1, 5, 10, 20, 40)
METHOD = ('買賣方向分開驗證：買方只使用該分點買入同股後 5／10 日的成本後上漲勝率，賣方只使用賣出後 5／10 日的成本後下跌勝率；'
          '完整清倉勝率只列為旁證，不混入方向可信分。至少一個同向分點具足夠樣本且可信分數達 55 才入選；'
          '跟買時，隔日沖占比高且缺少 5 日延續勝率者排除。買方另須 RS20>60、RS YTD≥70，'
          '淨買賣超張數與金額只作證據及同分排序，不參與主要分數。名單保存後不重算。下一交易日開盤進場，'
          '第 1／5／10／20／40 個交易日收盤評分；賣方採價格反向報酬，成本後結果扣除一般股票來回費稅與滑價估計，未含借券成本。')
V1_METHOD = ('V1 沿用高勝率分點與 RS 選股；加權指數與櫃買指數收盤同時低於各自 SMA20 時暫停建立新部位。'
             '允許進場時於下一交易日開盤買入，進場後觸及 +8% 停利或 -8% 停損；若開盤已跳過門檻，'
             '按實際開盤價出場。同一日同時碰到兩側時保守採停損，未觸及則於第 20 個交易日收盤出場，'
             '績效扣除一般股票來回成本估計。')
V4_METHOD = ('V4-B 只做多：沿用 V1 入選條件，以 92% V1 分數＋8% 同日族群價格趨勢重排。'
             '下一交易日若開高則於開盤買進，'
             '開低或平盤則以訊號日收盤下方 5% 為進場價，最多等待 5 個交易日，成交後持有 20 個交易日。')
V5_METHOD = ('V5 使用台積電 BIAS20＝（收盤價÷20 日收盤均價－1）×100。收盤確認台積電 BIAS20<0% 後，'
             '於下一交易日開盤買入國泰臺灣加權正2（00663L）；台積電 BIAS20≥6% 後，於下一交易日開盤賣出。'
             '每個負 BIAS20 日期視為一筆等額訊號，回測已扣 ETF 交易成本估計，持有中以最新收盤價評價。')


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _month_starts(start,end):
    cursor=date.fromisoformat(start).replace(day=1);last=date.fromisoformat(end).replace(day=1);result=[]
    while cursor<=last:
        result.append(cursor.isoformat())
        cursor=(cursor+timedelta(days=32)).replace(day=1)
    return result


def _parse_tpex_index(payload,month):
    """Validate the official TPEx monthly index response and return daily OHLC rows."""
    if isinstance(payload,(bytes,bytearray)):payload=json.loads(payload.decode('utf-8'))
    if not isinstance(payload,dict) or not isinstance(payload.get('tables'),list):
        raise ValueError('櫃買指數資料格式改變')
    table=next((item for item in payload['tables'] if isinstance(item,dict) and isinstance(item.get('data'),list)),None)
    if not table:raise ValueError('櫃買指數月份沒有日線資料')
    fields=table.get('fields') or []
    try:positions={key:fields.index(label) for key,label in {'date':'日期','open':'開市','high':'最高','low':'最低','close':'收市'}.items()}
    except ValueError:raise ValueError('櫃買指數欄位改變') from None
    expected=month[:7];rows=[]
    for source in table['data']:
        if not isinstance(source,list) or len(source)<=max(positions.values()):continue
        day=str(source[positions['date']]).replace('/','-')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or not day.startswith(expected+'-'):continue
        try:values={key:float(str(source[index]).replace(',','')) for key,index in positions.items() if key!='date'}
        except (TypeError,ValueError):continue
        if any(not math.isfinite(value) or value<=0 for value in values.values()):continue
        rows.append({'date':day,**values})
    if not rows:raise ValueError(f'櫃買指數 {expected} 沒有可驗證日線')
    if len({row['date'] for row in rows})!=len(rows):raise ValueError('櫃買指數日期重複')
    return sorted(rows,key=lambda row:row['date'])


def _fetch_tpex_index_month(month):
    try:
        response=requests.post(TPEX_INDEX_URL,data={'date':month[:7].replace('-','/')+'/01'},timeout=(12,35),headers={
            'User-Agent':'Mozilla/5.0 (compatible; LocalBranchMonitor/1.0)',
            'Accept':'application/json','Referer':'https://www.tpex.org.tw/zh-tw/indices/stock-index/industrial/inxh.html',
        })
        response.raise_for_status()
        return _parse_tpex_index(response.content,month)
    except requests.RequestException as error:
        raise ValueError(f'櫃買指數來源連線失敗：{type(error).__name__}') from error


def _refresh_tpex_index(start,end):
    """Fill missing official TPEx index months; always refresh the latest month."""
    months=_month_starts(start,end);target_month=end[:7]
    with db.connect() as c:
        existing={row[0] for row in c.execute("SELECT DISTINCT substr(date,1,7) FROM prices WHERE symbol='TPEX'")}
    requested=[month for month in months if month[:7] not in existing or month[:7]==target_month]
    saved=0
    for month in requested:
        rows=_fetch_tpex_index_month(month)
        with db.connect() as c:
            for row in rows:
                if row['date']>end:continue
                c.execute('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',
                          (row['date'],'TPEX','櫃買指數',row['close'],row['open'],row['high'],row['low'],None,'TPEX'))
                saved+=1
    return {'requested_months':len(requested),'saved_rows':saved,'through':end}


def _sma20_by_date(rows):
    window=deque();result={}
    for source in sorted(rows,key=lambda row:row['date']):
        close=source.get('close')
        if not isinstance(close,(int,float)) or not math.isfinite(close) or close<=0:continue
        window.append(float(close))
        if len(window)>20:window.popleft()
        if len(window)==20:result[source['date']]={'close':float(close),'sma20':sum(window)/20}
    return result


def _dual_index_sma20_filter(taiex_rows,tpex_rows):
    taiex=_sma20_by_date(taiex_rows);tpex=_sma20_by_date(tpex_rows);states={}
    for day in sorted(set(taiex)&set(tpex)):
        tw=taiex[day];otc=tpex[day];blocked=tw['close']<tw['sma20'] and otc['close']<otc['sma20']
        states[day]={'date':day,'available':True,'blocked':blocked,
                     'taiex_close':round(tw['close'],2),'taiex_sma20':round(tw['sma20'],2),
                     'tpex_close':round(otc['close'],2),'tpex_sma20':round(otc['sma20'],2)}
    return states


def _apply_v1_market_filter(payload,state):
    payload['v1_market_filter']=state or {'date':payload.get('date'),'available':False,'blocked':False}
    payload['blocked_buy']=[]
    if not state or not state.get('blocked'):return payload
    payload['blocked_buy']=payload.get('buy',[]);payload['buy']=[]
    warning='V1 暫停買進：加權指數與櫃買指數收盤皆低於各自 SMA20'
    if warning not in payload.setdefault('warnings',[]):payload['warnings'].append(warning)
    return payload


def _percentiles(values):
    """Return 0..100 midrank percentiles while keeping ties equal."""
    if not values:return []
    order=sorted(range(len(values)),key=lambda i:values[i]);result=[0.]*len(values);i=0;n=len(values)
    while i<n:
        j=i+1
        while j<n and abs(values[order[j]]-values[order[i]])<1e-9:j+=1
        score=100*((i+j-1)/2)/(n-1) if n>1 else 50.
        for k in order[i:j]:result[k]=score
        i=j
    return result


def _sector_scores(day,memberships):
    """Point-in-time price trend by sector; never reads prices after *day*."""
    groups=defaultdict(list)
    for symbol,names in memberships.items():
        for name in names:groups[name].append(symbol)
    if not groups:return {}
    with db.connect() as c:
        days=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date<=? ORDER BY date DESC LIMIT 6",(day,))][::-1]
        if len(days)<6 or days[-1]!=day:return {}
        wanted={symbol for symbols in groups.values() for symbol in symbols}
        marks=','.join('?' for _ in days)
        prices={(r['date'],r['symbol']):r['close'] for r in c.execute(
            f'SELECT date,symbol,close FROM prices WHERE date IN ({marks})',days) if r['symbol'] in wanted}
    rows=[]
    for name,symbols in groups.items():
        if not 3<=len(symbols)<=30:continue
        r1=[];r5=[]
        for symbol in symbols:
            current=prices.get((days[-1],symbol));previous=prices.get((days[-2],symbol));start=prices.get((days[0],symbol))
            if current and previous:r1.append((current/previous-1)*100)
            if current and start:r5.append((current/start-1)*100)
        if len(r1)<3 or len(r5)<3:continue
        rows.append({'name':name,'r1':sum(r1)/len(r1),'r5':sum(r5)/len(r5),
                     'breadth':sum(value>0 for value in r1)*100/len(r1)})
    if not rows:return {}
    p1=_percentiles([row['r1'] for row in rows]);p5=_percentiles([row['r5'] for row in rows]);pb=_percentiles([row['breadth'] for row in rows])
    by_group={row['name']:.40*p1[i]+.35*p5[i]+.25*pb[i] for i,row in enumerate(rows)}
    result={}
    for symbol,names in memberships.items():
        available=[(by_group[name],name) for name in names if name in by_group]
        if available:
            score,name=max(available);result[symbol]={'score':score,'name':name}
    return result


def available_dates():
    with db.connect() as c:
        snapshots={r[0] for r in c.execute("""SELECT s.date FROM snapshots s JOIN branches b USING(broker,branch)
            WHERE b.active=1 AND s.status='observed' GROUP BY s.date""")}
        rs_days={r[0] for r in c.execute('SELECT date FROM rs_days')}
    return sorted(snapshots & rs_days,reverse=True)


def _rs_snapshot(day):
    with db.connect() as c:r=c.execute('SELECT date,payload FROM rs_days WHERE date=?',(day,)).fetchone()
    if not r:raise ValueError('此日期沒有同日 RS 快照，不能建立無前視偏誤的名單')
    payload=json.loads(r['payload'])
    return payload,{mode:{x['symbol']:x for x in payload.get('groups',{}).get(mode,[])} for mode in ('20','ytd')}


def _style(days):
    if days is None:return '天數待確認'
    if days<=0:return '0 日當沖'
    if days==1:return '1 日隔日沖'
    if days<=4:return '2–4 日極短線'
    if days<=20:return '5–20 日短線'
    return '21 日以上波段'


def _forward_record(flows,symbol,calendar,prices,side):
    indices={day:i for i,day in enumerate(calendar)};result={}
    signals=sorted({r['date'] for r in flows if r['symbol']==symbol and (r['net']>0 if side=='buy' else r['net']<0)})
    for horizon in (5,10):
        returns=[]
        for signal_day in signals:
            index=indices.get(signal_day)
            if index is None or index+horizon>=len(calendar):continue
            end=calendar[index+horizon];start_price=prices.get((signal_day,symbol));end_price=prices.get((end,symbol))
            if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (start_price,end_price)):continue
            raw=(end_price/start_price-1)*100
            returns.append((raw if side=='buy' else -raw)-ROUND_TRIP_PCT)
        result[str(horizon)]={'samples':len(returns),'wins':sum(v>0 for v in returns),
            'win_rate':sum(v>0 for v in returns)*100/len(returns) if returns else None,
            'avg_return_pct':sum(returns)/len(returns) if returns else None}
    return result


def _direction_score(forward,side,dominant):
    samples=forward['5']['samples']+forward['10']['samples'];wins=forward['5']['wins']+forward['10']['wins']
    raw=wins*100/samples if samples else None;bayes=(wins+1)*100/(samples+2) if samples else None
    overnight=side=='buy' and dominant=='1 日隔日沖'
    penalty=15 if overnight else 0;quality=max(0,(bayes or 0)-penalty) if bayes is not None else None
    compatible=not overnight or (forward['5']['samples']>=3 and (forward['5']['win_rate'] or 0)>=60)
    provisional=samples>=3 and quality is not None and quality>=55 and compatible
    return {'direction':side,'win_rate':raw,'quality_score':quality,'effective_samples':samples,
        'forward_5':forward['5'],'forward_10':forward['10'],'style_penalty':penalty,
        'style_compatible':compatible,'provisional_qualified':provisional,'qualified':provisional}


def _completed_cycles(rows,price_map,day_index,verified):
    if not verified:return []
    lots=deque();cycle=None;finished=[]
    for row in rows:
        quantity=float(row['net']);price=price_map.get((row['date'],row['symbol']))
        if quantity>1e-9:
            if not lots:cycle={'cost':0.,'pnl':0.,'matched':0.,'weighted_days':0.,'invalid':False,'start':row['date']}
            lots.append({'date':row['date'],'quantity':quantity,'price':price})
            if price is None:cycle['invalid']=True
            else:cycle['cost']+=quantity*price
        elif quantity < -1e-9:
            remaining=-quantity;matched=0.
            while remaining>1e-9 and lots:
                lot=lots[0];take=min(remaining,lot['quantity']);matched+=take
                if price is None or lot['price'] is None:cycle['invalid']=True
                else:cycle['pnl']+=take*(price-lot['price'])
                if row['date'] not in day_index or lot['date'] not in day_index:cycle['invalid']=True
                else:cycle['weighted_days']+=take*(day_index[row['date']]-day_index[lot['date']])
                cycle['matched']+=take;lot['quantity']-=take;remaining-=take
                if lot['quantity']<1e-9:lots.popleft()
            if cycle and matched>0 and not lots:
                if remaining>1e-9:cycle['invalid']=True
                if not cycle['invalid'] and cycle['cost']>0 and cycle['matched']>0:
                    finished.append({'return_pct':cycle['pnl']/cycle['cost']*100,
                        'holding_days':cycle['weighted_days']/cycle['matched']})
                cycle=None
    return finished


def _branch_track_records(day,needed_keys):
    """Only evidence strictly before the signal day can qualify a branch."""
    needed_keys=set(needed_keys);symbols={key[2] for key in needed_keys}
    if not needed_keys:return {}
    needed_by_branch=defaultdict(set)
    for broker,branch,symbol in needed_keys:needed_by_branch[(broker,branch)].add(symbol)
    with db.connect() as c:
        branches={(r['broker'],r['branch']):r['name'] for r in c.execute('SELECT * FROM branches WHERE active=1')}
        calendar=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date<? ORDER BY date",(day,))]
        flows=[];intervals=[]
        for (broker,branch),wanted in needed_by_branch.items():
            flows.extend(dict(r) for r in c.execute("""SELECT * FROM flows WHERE broker=? AND branch=? AND date<?
                ORDER BY symbol,date""",(broker,branch,day)) if r['symbol'] in wanted)
            intervals.extend(dict(r) for r in c.execute("""SELECT * FROM history_intervals WHERE broker=? AND branch=? AND end>=?""",
                (broker,branch,calendar[-1] if calendar else day)) if r['symbol'] in wanted)
        first=min((r['date'] for r in flows),default=day)
        placeholders=','.join('?' for _ in symbols)
        prices=[dict(r) for r in c.execute(f'SELECT date,symbol,close FROM prices WHERE date>=? AND date<? AND symbol IN ({placeholders})',(first,day,*sorted(symbols)))]
    by_key=defaultdict(list);by_interval=defaultdict(list)
    for row in flows:by_key[(row['broker'],row['branch'],row['symbol'])].append(row)
    for row in intervals:by_interval[(row['broker'],row['branch'],row['symbol'])].append(row)
    price_map={(r['date'],r['symbol']):r['close'] for r in prices};records={};cutoff=calendar[-1] if calendar else day;day_index={d:i for i,d in enumerate(calendar)}
    for key in needed_keys:
        rows=by_key[key]
        if not rows:continue
        sessions=[d for d in calendar if rows[0]['date']<=d<=cutoff];ranges=by_interval[key]
        verified=bool(sessions) and all(any(x['start']<=session<=x['end'] for x in ranges) for session in sessions)
        finished=_completed_cycles(rows,price_map,day_index,verified);cycle_returns=[r['return_pct']-ROUND_TRIP_PCT for r in finished]
        cycle_wins=sum(v>0 for v in cycle_returns)
        styles=Counter(_style(r.get('holding_days')) for r in finished);dominant=styles.most_common(1)[0][0] if styles else '尚無完整清倉'
        overnight=sum(r.get('holding_days')==1 for r in finished);overnight_share=overnight*100/len(finished) if finished else 0
        buy=_direction_score(_forward_record(rows,key[2],calendar,price_map,'buy'),'buy',dominant)
        sell=_direction_score(_forward_record(rows,key[2],calendar,price_map,'sell'),'sell',dominant)
        buy['qualified'] &= verified;sell['qualified'] &= verified
        records[key]={'name':branches.get((key[0],key[1]),key[0]+'-'+key[1]),'history_verified':verified,
            'cycle_win_rate':cycle_wins*100/len(cycle_returns) if cycle_returns else None,'cycle_samples':len(cycle_returns),
            'cycle_avg_return_pct':sum(cycle_returns)/len(cycle_returns) if cycle_returns else None,
            'dominant_style':dominant,'overnight_share':overnight_share,'buy':buy,'sell':sell}
    return records


def _estimated_open_position(rows,price_map):
    """Estimate the branch's remaining long inventory with FIFO lots."""
    lots=deque()
    for row in rows:
        quantity=float(row.get('net') or 0)
        if quantity>1e-9:
            lots.append({'quantity':quantity,'price':price_map.get((row['date'],row['symbol']))})
            continue
        remaining=-quantity
        while remaining>1e-9 and lots:
            lot=lots[0];take=min(remaining,lot['quantity'])
            lot['quantity']-=take;remaining-=take
            if lot['quantity']<1e-9:lots.popleft()
    quantity=sum(lot['quantity'] for lot in lots)
    if quantity<1e-9:
        return {'average_cost':None,'estimated_position':0.,'average_cost_status':'no_position'}
    if any(lot['price'] is None for lot in lots):
        return {'average_cost':None,'estimated_position':round(quantity,4),'average_cost_status':'missing_price'}
    cost=sum(lot['quantity']*float(lot['price']) for lot in lots)
    return {'average_cost':round(cost/quantity,4),'estimated_position':round(quantity,4),
            'average_cost_status':'available'}


def _attach_branch_average_costs(payload):
    """Add point-in-time branch cost estimates without changing frozen picks."""
    day=payload.get('date')
    # Older payloads keep symbol on the signal, not on each branch proof.
    proof_targets=[]
    for side in ('buy','sell'):
        for signal in payload.get(side,[]):
            for field in ('branches','qualified_branches'):
                proof_targets.extend((proof,(proof.get('broker'),proof.get('branch'),signal.get('symbol')))
                                     for proof in signal.get(field,[]))
        for signal in payload.get('v4',{}).get(side,[]):
            for field in ('branches','qualified_branches'):
                proof_targets.extend((proof,(proof.get('broker'),proof.get('branch'),signal.get('symbol')))
                                     for proof in signal.get(field,[]))
    targets={key for _,key in proof_targets if day and all(key)}
    if not targets:return False
    by_branch=defaultdict(set)
    for broker,branch,symbol in targets:by_branch[(broker,branch)].add(symbol)
    flows=[]
    with db.connect() as c:
        for (broker,branch),symbols in by_branch.items():
            flows.extend(dict(row) for row in c.execute(
                'SELECT broker,branch,date,symbol,net FROM flows WHERE broker=? AND branch=? AND date<=? ORDER BY date,symbol',
                (broker,branch,day)) if row['symbol'] in symbols)
        symbols=sorted({key[2] for key in targets});marks=','.join('?' for _ in symbols)
        prices=[dict(row) for row in c.execute(
            f'SELECT date,symbol,close FROM prices WHERE date<=? AND symbol IN ({marks})',(day,*symbols))]
    by_key=defaultdict(list)
    for row in flows:by_key[(row['broker'],row['branch'],row['symbol'])].append(row)
    price_map={(row['date'],row['symbol']):row['close'] for row in prices}
    estimates={key:_estimated_open_position(by_key.get(key,[]),price_map) for key in targets}
    changed=False
    for proof,key in proof_targets:
        estimate=estimates.get(key)
        if not estimate:continue
        if any(proof.get(field)!=value for field,value in estimate.items()):
            proof.update(estimate);changed=True
        proof['average_cost_as_of']=day
        proof['average_cost_method']='observed_fifo_close'
    return changed


def _rank(rows,side,memberships,rs_maps,track_records,limit=5,require_verified=True):
    selected=[]
    for row in rows:
        if (side=='buy' and row['net']<=0) or (side=='sell' and row['net']>=0):continue
        r20=rs_maps['20'].get(row['symbol']);ry=rs_maps['ytd'].get(row['symbol'])
        if not r20 or not ry:continue
        if side=='buy' and not (r20['rs']>60 and ry['rs']>=70):continue
        direction=row['buyers' if side=='buy' else 'sellers'];qualified=[]
        for branch in direction:
            base=track_records.get((branch['broker'],branch['branch'],row['symbol']));record=base.get(side) if base else None
            provisional=(record.get('provisional_qualified') if record else False)
            if record and (record['qualified'] if require_verified else provisional):
                qualified.append({**branch,**record,'cycle_win_rate':base['cycle_win_rate'],'cycle_samples':base['cycle_samples'],
                    'cycle_avg_return_pct':base['cycle_avg_return_pct'],'dominant_style':base['dominant_style'],
                    'overnight_share':base['overnight_share'],'history_verified':base['history_verified']})
        if not qualified:continue
        weights=[math.sqrt(r['effective_samples']) for r in qualified]
        trust=sum(r['quality_score']*w for r,w in zip(qualified,weights))/sum(weights)
        qualified.sort(key=lambda r:(-r['quality_score'],-r['effective_samples'],r['name']))
        selected.append(dict(row,rs20=float(r20['rs']),rs_ytd=float(ry['rs']),return20=r20.get('return_pct'),
            ytd_pct=ry.get('ytd_pct'),sectors=memberships.get(row['symbol'],[])[:4] or ['未分類'],
            trust_score=trust,qualified_branches=qualified,total_direction_branches=len(direction),
            ignored_branch_count=len(direction)-len(qualified)))
    if not selected:return []
    credible=[len(r['qualified_branches']) for r in selected];cp=_percentiles(credible)
    for i,row in enumerate(selected):
        r20=row['rs20'] if side=='buy' else 100-row['rs20'];ry=row['rs_ytd'] if side=='buy' else 100-row['rs_ytd']
        coverage=len(row['qualified_branches'])/max(1,row['total_direction_branches'])*100
        row['score']=round(.55*row['trust_score']+.15*cp[i]+.10*coverage+.10*r20+.10*ry,1)
        row['trust_score']=round(row['trust_score'],1);row['credible_branch_percentile']=round(cp[i],1)
        row['branches']=row['qualified_branches'][:5]
        for key in ('buyers','sellers'):row.pop(key,None)
    selected.sort(key=lambda r:(-r['score'],-r['trust_score'],-len(r['qualified_branches']),-abs(r['net']),r['symbol']))
    return selected if limit is None else selected[:limit]


def _targeted_history_plan(day,candidate_limit=12):
    """Select only high-quality latest candidates whose verified tail is stale."""
    if not isinstance(candidate_limit,int) or isinstance(candidate_limit,bool) or not 5<=candidate_limit<=20:
        raise ValueError('每日跟單驗證候選需為 5～20 檔')
    consensus=broker_focus.consensus(day);_payload,rs_maps=_rs_snapshot(day)
    dictionary=sector_flow.classification();memberships=dictionary.get('memberships',{})
    needed={(branch['broker'],branch['branch'],row['symbol']) for row in consensus['rows']
            for side in ('buyers','sellers') for branch in row[side]}
    records=_branch_track_records(day,needed)
    provisional=[]
    for side in ('buy','sell'):
        provisional.extend((side,row) for row in _rank(consensus['rows'],side,memberships,rs_maps,records,
                                                        limit=candidate_limit,require_verified=False))
    selected={}
    for side,row in provisional:
        for branch in row['qualified_branches']:
            if branch.get('history_verified'):continue
            key=(branch['broker'],branch['branch'],row['symbol'])
            selected.setdefault(key,{'broker':branch['broker'],'branch':branch['branch'],'branch_name':branch['name'],
                                     'symbol':row['symbol'],'name':row['name'],'sides':set()})
            selected[key]['sides'].add(side)
    with db.connect() as c:
        calendar=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date<? ORDER BY date",(day,))]
        cutoff=calendar[-1] if calendar else None
        if not cutoff:raise ValueError('沒有前一交易日，無法建立每日跟單驗證區間')
        plan=[]
        for item in selected.values():
            key=(item['broker'],item['branch'],item['symbol'])
            covered=c.execute('''SELECT 1 FROM history_intervals WHERE broker=? AND branch=? AND symbol=?
                                 AND start<=? AND end>=?''',(*key,cutoff,cutoff)).fetchone() if cutoff else None
            if covered:continue
            # Include the latest known transaction as an overlap anchor. This
            # lets an empty new tail be verified without pretending that an
            # empty source page proves zero trading.
            anchor=c.execute('''SELECT MAX(date) FROM flows WHERE broker=? AND branch=? AND symbol=? AND date<?''',(*key,day)).fetchone()[0]
            if not anchor:continue
            plan.append({**item,'sides':sorted(item['sides']),'start':anchor,'end':cutoff})
    return {'day':day,'cutoff':cutoff,'candidate_limit':candidate_limit,'candidates':len(provisional),
            'rows':sorted(plan,key=lambda row:(row['branch_name'],row['symbol']))}


def refresh_latest_history(day=None,candidate_limit=12):
    """Repair a bounded verified tail for the latest daily-follow shortlist."""
    from . import service,sources
    dates=available_dates()
    day=day or (dates[0] if dates else None)
    if not day or day not in dates:raise ValueError('分點與 RS 尚無同日快照，無法驗證每日跟單候選')
    plan=_targeted_history_plan(day,candidate_limit)
    if not service._sync_lock.acquire(blocking=False):raise ValueError('每日同步或歷史修復正在執行，稍後再驗證跟單候選')
    succeeded=0;errors=[]
    try:
        for item in plan['rows']:
            try:
                rows=sources.history(item['broker'],item['branch'],item['symbol'],item['name'],item['start'],item['end'],refresh=True)
                service._store_history(item['broker'],item['branch'],item['symbol'],item['start'],item['end'],rows)
                succeeded+=1
            except Exception as error:
                errors.append(f"{item['branch_name']} {item['name']} {item['symbol']}：{str(error)[:120]}")
    finally:service._sync_lock.release()
    # Recalculate locally so the caller can distinguish genuine scarcity from
    # unresolved verification gaps before freezing the daily list.
    consensus=broker_focus.consensus(day);_payload,rs_maps=_rs_snapshot(day)
    memberships=sector_flow.classification().get('memberships',{})
    needed={(branch['broker'],branch['branch'],row['symbol']) for row in consensus['rows']
            for side in ('buyers','sellers') for branch in row[side]}
    records=_branch_track_records(day,needed)
    buys=_rank(consensus['rows'],'buy',memberships,rs_maps,records,limit=None)
    sells=_rank(consensus['rows'],'sell',memberships,rs_maps,records,limit=None)
    return {**plan,'planned':len(plan['rows']),'succeeded':succeeded,'failed':len(errors),'errors':errors,
            'buy_ready':len(buys),'sell_ready':len(sells),
            'status':'partial' if errors else 'complete'}


def _v4_rank(day,candidates,memberships,side='buy'):
    sector_scores=_sector_scores(day,memberships);ranked=[]
    for source in candidates:
        row=dict(source);sector=sector_scores.get(row['symbol'],{'score':50.,'name':'未分類'})
        directional=sector['score'] if side=='buy' else 100-sector['score']
        row['base_score']=row['score'];row['sector_score']=round(directional,1);row['sector_name']=sector['name']
        row['score']=round((1-V4_SECTOR_WEIGHT)*row['base_score']+V4_SECTOR_WEIGHT*row['sector_score'],1)
        ranked.append(row)
    ranked.sort(key=lambda r:(-r['score'],-r['trust_score'],-len(r['qualified_branches']),-abs(r['net']),r['symbol']))
    return ranked[:5]


def _mode(day,created_at=None):
    """Forward only when frozen before the next known or conservatively inferred open."""
    with db.connect() as c:later=c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date>? ORDER BY date LIMIT 1",(day,)).fetchone()
    if later:next_day=date.fromisoformat(later[0])
    else:
        next_day=date.fromisoformat(day)+timedelta(days=1)
        while next_day.weekday()>4:next_day+=timedelta(days=1)
    created=datetime.fromisoformat(created_at) if created_at else datetime.now(TZ)
    if created.tzinfo is None:created=created.replace(tzinfo=TZ)
    deadline=datetime.combine(next_day,time(8,45),TZ)
    return 'forward' if created<deadline else 'replay'


def _v4_block(day,buy_candidates,sell_candidates,memberships,dictionary):
    return {'version':V4_VERSION,'date':day,'buy':_v4_rank(day,buy_candidates,memberships),
            'sell':_v4_rank(day,sell_candidates,memberships,'sell'),
            'sector_weight_pct':round(V4_SECTOR_WEIGHT*100,1),'sector_snapshot':dictionary.get('snapshot'),
            'hold_sessions':V4_HOLD,'wait_sessions':V4_WAIT,'pullback_pct':round(V4_PULLBACK*100,1),
            'method':V4_METHOD}


def _attach_v4(day,payload):
    consensus=broker_focus.consensus(day);_rs_payload,rs_maps=_rs_snapshot(day)
    dictionary=sector_flow.classification();memberships=dictionary.get('memberships',{})
    needed={(branch['broker'],branch['branch'],row['symbol']) for row in consensus['rows'] for side in ('buyers','sellers') for branch in row[side]}
    records=_branch_track_records(day,needed)
    buy_candidates=_rank(consensus['rows'],'buy',memberships,rs_maps,records,limit=None)
    sell_candidates=_rank(consensus['rows'],'sell',memberships,rs_maps,records,limit=None)
    if payload.get('v4'):
        payload['v4']['sell']=_v4_rank(day,sell_candidates,memberships,'sell')
    else:payload['v4']=_v4_block(day,buy_candidates,sell_candidates,memberships,dictionary)
    _attach_branch_average_costs(payload)
    with db.connect() as c:c.execute('UPDATE daily_follow_reports SET payload=? WHERE date=?',(json.dumps(payload,ensure_ascii=False),day))
    return payload


def generate(day=None,overwrite=False):
    dates=available_dates()
    if not dates:raise ValueError('分點與 RS 尚無同日快照，請先更新這兩項資料')
    day=day or dates[0]
    if day not in dates:raise ValueError('此日期沒有同日分點與 RS 快照')
    with db.connect() as c:saved=c.execute('SELECT payload FROM daily_follow_reports WHERE date=?',(day,)).fetchone()
    if saved and not overwrite:
        payload=json.loads(saved['payload'])
        if not payload.get('v4') or 'sell' not in payload['v4']:payload=_attach_v4(day,payload)
        if _attach_branch_average_costs(payload):
            with db.connect() as c:c.execute('UPDATE daily_follow_reports SET payload=? WHERE date=?',(json.dumps(payload,ensure_ascii=False),day))
        return payload
    consensus=broker_focus.consensus(day);rs_payload,rs_maps=_rs_snapshot(day)
    dictionary=sector_flow.classification();memberships=dictionary.get('memberships',{})
    needed={(branch['broker'],branch['branch'],row['symbol']) for row in consensus['rows'] for side in ('buyers','sellers') for branch in row[side]}
    track_records=_branch_track_records(day,needed)
    buy_candidates=_rank(consensus['rows'],'buy',memberships,rs_maps,track_records,limit=None)
    buys=buy_candidates[:5];sell_candidates=_rank(consensus['rows'],'sell',memberships,rs_maps,track_records,limit=None);sells=sell_candidates[:5]
    provisional_buys=_rank(consensus['rows'],'buy',memberships,rs_maps,track_records,limit=None,require_verified=False)
    provisional_sells=_rank(consensus['rows'],'sell',memberships,rs_maps,track_records,limit=None,require_verified=False)
    warnings=[]
    if len(buys)<5:
        warnings.append((f'買方有 {len(provisional_buys)} 檔通過 RS 與既有勝率，但分點歷史驗證尚未延伸至前一交易日'
                         if len(provisional_buys)>len(buys) else f'買方符合 RS 與分點條件僅 {len(buys)} 檔，未以低品質標的補滿'))
    if len(sells)<5:
        warnings.append((f'賣方有 {len(provisional_sells)} 檔通過既有勝率，但分點歷史驗證尚未延伸至前一交易日'
                         if len(provisional_sells)>len(sells) else f'賣方可評分標的僅 {len(sells)} 檔，未以缺少 RS 的標的補滿'))
    created_at=now();mode=_mode(day,created_at)
    payload=dict(date=day,created_at=created_at,mode=mode,version=VERSION,rs_date=day,
                 coverage={'observed':consensus['observed'],'total':consensus['total'],'missing':consensus['missing']},
                 index_ytd=rs_payload.get('index_ytd'),buy=buys,sell=sells,warnings=warnings,
                 v4=_v4_block(day,buy_candidates,sell_candidates,memberships,dictionary),
                 sector_snapshot=dictionary.get('snapshot'),method=METHOD,round_trip_cost_pct=round(ROUND_TRIP_PCT,4))
    _attach_branch_average_costs(payload)
    with db.connect() as c:c.execute('INSERT OR REPLACE INTO daily_follow_reports VALUES (?,?,?,?,?)',(day,payload['created_at'],mode,day,json.dumps(payload,ensure_ascii=False)))
    return payload


def generate_all(start=HISTORY_START):
    rs_backfill=rs.backfill_local_days(start)
    dates=available_dates();created=[]
    tpex_index=_refresh_tpex_index(TPEX_INDEX_START,dates[0]) if dates else {'requested_months':0,'saved_rows':0,'through':None}
    with db.connect() as c:saved={r['date']:json.loads(r['payload']) for r in c.execute('SELECT date,payload FROM daily_follow_reports')}
    for day in reversed(dates):
        if day not in saved or not saved[day].get('v4') or 'sell' not in saved[day]['v4']:generate(day);created.append(day)
    return {'dates':dates,'created':created,'latest':dates[0] if dates else None,'rs_backfill':rs_backfill,
            'tpex_index':tpex_index}


def _evaluate(signal,report_date,side,calendar,quotes):
    pending=lambda:{'status':'pending','exit_date':None,'gross_return_pct':None,'net_return_pct':None,'hit':None}
    base=dict(status='pending',entry_date=None,entry_price=None,
              horizons={str(horizon):pending() for horizon in BACKTEST_HORIZONS})
    future=[d for d in calendar if d>report_date]
    if not future:return base
    entry_date=future[0];entry=quotes.get((entry_date,signal['symbol']))
    if not entry or not entry.get('open'):
        return dict(base,status='gap',entry_date=entry_date,message='下一交易日缺少開盤價')
    base.update(status='tracking',entry_date=entry_date,entry_price=entry['open'])
    for horizon in BACKTEST_HORIZONS:
        if len(future)<horizon:
            continue
        exit_date=future[horizon-1];exit_row=quotes.get((exit_date,signal['symbol']))
        if not exit_row or not exit_row.get('close'):
            base['horizons'][str(horizon)]={'status':'gap','exit_date':exit_date,'gross_return_pct':None,'net_return_pct':None,'hit':None}
            continue
        raw=(exit_row['close']/entry['open']-1)*100;directional=raw if side=='buy' else -raw
        net=directional-ROUND_TRIP_PCT
        base['horizons'][str(horizon)]={'status':'scored','exit_date':exit_date,'exit_price':exit_row['close'],
            'gross_return_pct':round(directional,4),'net_return_pct':round(net,4),'hit':net>0}
    return base


def _evaluate_v1(signal,report_date,calendar,quotes):
    base={'status':'pending','entry_date':None,'entry_price':None,'exit_date':None,'exit_price':None,
          'exit_reason':None,'gross_return_pct':None,'net_return_pct':None,'hit':None,'holding_sessions':None}
    future=[day for day in calendar if day>report_date]
    if not future:return base
    entry=quotes.get((future[0],signal['symbol'])) or {};entry_price=entry.get('open')
    if not isinstance(entry_price,(int,float)) or not math.isfinite(entry_price) or entry_price<=0:
        return {**base,'status':'gap','entry_date':future[0],'message':'下一交易日缺少開盤價'}
    result={**base,'status':'tracking','entry_date':future[0],'entry_price':float(entry_price)}
    target=float(entry_price)*(1+V1_TAKE_PROFIT);stop=float(entry_price)*(1-V1_STOP_LOSS)
    for index,day in enumerate(future[:V4_HOLD],1):
        quote=quotes.get((day,signal['symbol'])) or {};open_price=quote.get('open');high=quote.get('high');low=quote.get('low')
        valid_open=isinstance(open_price,(int,float)) and math.isfinite(open_price)
        hit_stop=isinstance(low,(int,float)) and math.isfinite(low) and low<=stop
        hit_target=isinstance(high,(int,float)) and math.isfinite(high) and high>=target
        if valid_open and open_price<=stop:exit_price=float(open_price);reason='stop_loss_gap'
        elif valid_open and open_price>=target:exit_price=float(open_price);reason='take_profit_gap'
        elif hit_stop:exit_price=stop;reason='stop_loss'
        elif hit_target:exit_price=target;reason='take_profit'
        else:continue
        gross=(exit_price/float(entry_price)-1)*100;net=gross-ROUND_TRIP_PCT
        return {**result,'status':'scored','exit_date':day,'exit_price':round(exit_price,4),'exit_reason':reason,
                'gross_return_pct':round(gross,4),'net_return_pct':round(net,4),'hit':net>0,'holding_sessions':index}
    if len(future)<V4_HOLD:return result
    exit_date=future[V4_HOLD-1];exit_row=quotes.get((exit_date,signal['symbol'])) or {};exit_price=exit_row.get('close')
    if not isinstance(exit_price,(int,float)) or not math.isfinite(exit_price) or exit_price<=0:
        return {**result,'status':'gap','exit_date':exit_date,'message':'20 日收盤價缺漏'}
    gross=(float(exit_price)/float(entry_price)-1)*100;net=gross-ROUND_TRIP_PCT
    return {**result,'status':'scored','exit_date':exit_date,'exit_price':float(exit_price),'exit_reason':'day20',
            'gross_return_pct':round(gross,4),'net_return_pct':round(net,4),'hit':net>0,'holding_sessions':V4_HOLD}


def _evaluate_v4(signal,report_date,strategy,calendar,quotes):
    base={'status':'pending','entry_kind':None,'entry_date':None,'entry_price':None,'wait_sessions':None,
          'exit_date':None,'exit_price':None,'net_return_pct':None,'hit':None}
    future=[d for d in calendar if d>report_date]
    if not future:return base
    reference=signal.get('close') or (quotes.get((report_date,signal['symbol'])) or {}).get('close')
    if not reference:return {**base,'status':'gap','message':'訊號日缺少收盤價'}
    first=quotes.get((future[0],signal['symbol']))
    if not first or not first.get('open'):return {**base,'status':'gap','message':'下一交易日缺少開盤價'}
    entry=None
    if strategy!='hybrid20':raise ValueError('未知的 V4 進場規則')
    if first['open']>reference:entry=(future[0],first['open'],1,'up_open')
    else:
        target=reference*(1-V4_PULLBACK)
        for wait,entry_day in enumerate(future[:V4_WAIT],1):
            quote=quotes.get((entry_day,signal['symbol']))
            if not quote or quote.get('low') is None:continue
            if quote.get('open') is not None and quote['open']<=target:
                entry=(entry_day,quote['open'],wait,'gap_pullback');break
            if quote['low']<=target:
                entry=(entry_day,target,wait,'pullback');break
        if entry is None:
            status='pending' if len(future)<V4_WAIT else 'unfilled'
            return {**base,'status':status,'entry_kind':'waiting_pullback','target_price':round(target,4)}
    entry_index=calendar.index(entry[0]);exit_index=entry_index+V4_HOLD-1
    result={**base,'status':'tracking','entry_date':entry[0],'entry_price':entry[1],
            'wait_sessions':entry[2],'entry_kind':entry[3]}
    if exit_index>=len(calendar):return result
    exit_date=calendar[exit_index];exit_row=quotes.get((exit_date,signal['symbol']))
    if not exit_row or not exit_row.get('close'):return {**result,'status':'gap','exit_date':exit_date,'message':'20 日收盤價缺漏'}
    raw=(exit_row['close']/entry[1]-1)*100;net=raw-ROUND_TRIP_PCT
    return {**result,'status':'scored','exit_date':exit_date,'exit_price':exit_row['close'],
            'gross_return_pct':round(raw,4),'net_return_pct':round(net,4),'hit':net>0}


def _v4_summary(reports,calendar,quotes):
    result={}
    for strategy,label in [('hybrid20','V4-B 開高買／開低等 -5%')]:
        values=[];eligible=entries=wins=0;per_report={}
        for report in reports:
            signals=report.get('v4',{}).get('buy',[]);future=[d for d in calendar if d>report['date']]
            if not signals or len(future)<V4_WAIT+V4_HOLD-1:continue
            day_values=[]
            for signal in signals:
                outcome=_evaluate_v4(signal,report['date'],strategy,calendar,quotes)
                if outcome['status']=='gap':continue
                eligible+=1
                if outcome['status']=='scored':
                    value=outcome['net_return_pct'];values.append(value);day_values.append(value);entries+=1;wins+=value>0
                elif outcome['status']=='unfilled':day_values.append(0.)
            if day_values:per_report[report['date']]=sum(day_values)/len(day_values)
        portfolio=sum(per_report.values())/len(per_report) if per_report else None
        result[strategy]={'label':label,'eligible_signals':eligible,'entries':entries,
            'fill_rate':round(entries*100/eligible,2) if eligible else None,'wins':wins,
            'hit_rate':round(wins*100/entries,2) if entries else None,
            'avg_return_pct':round(sum(values)/len(values),4) if values else None,
            'expected_return_pct':round(sum(values)/eligible,4) if eligible else None,
            'portfolio_return_pct':round(portfolio,4) if portfolio is not None else None,
            'report_days':len(per_report)}
    return result


def _model_day(report,model,calendar,quotes):
    """Return one frozen signal-day cohort using each model's actual entry rule."""
    future=[d for d in calendar if d>report['date']]
    required=V4_WAIT+V4_HOLD-1 if model=='v4b' else V4_HOLD
    if len(future)<required:return None
    if model=='v1':
        outcomes=[signal.get('v1_strategy') or _evaluate_v1(signal,report['date'],calendar,quotes) for signal in report.get('buy',[])]
    elif model=='v4b':
        outcomes=[_evaluate_v4(signal,report['date'],'hybrid20',calendar,quotes)
                  for signal in report.get('v4',{}).get('buy',[])]
    else:raise ValueError('未知的模型')
    eligible=[];filled=[]
    for outcome in outcomes:
        status=outcome.get('status')
        if status=='gap':continue
        if status=='scored':
            value=outcome.get('net_return_pct')
            if value is not None:
                eligible.append(float(value));filled.append(float(value))
        elif model=='v4b' and status=='unfilled':eligible.append(0.)
    if not eligible:return None
    return {'date':report['date'],'return_pct':sum(eligible)/len(eligible),'eligible':len(eligible),
            'entries':len(filled),'wins':sum(value>0 for value in filled),'filled_returns':filled}


def _model_result(key,label,description,days):
    values=[value for day in days for value in day['filled_returns']]
    eligible=sum(day['eligible'] for day in days);entries=sum(day['entries'] for day in days)
    total=0.;curve=[]
    for day in sorted(days,key=lambda row:row['date']):
        previous=total/len(curve) if curve else None;total+=day['return_pct'];value=total/(len(curve)+1)
        curve.append({'date':day['date'],'value':round(value,4),
                      'day_return_pct':round(day['return_pct'],4),'eligible':day['eligible'],
                      'entries':day['entries']})
    return {'key':key,'label':label,'description':description,'hold_sessions':V4_HOLD,
            'report_days':len(days),'eligible_signals':eligible,'entries':entries,
            'fill_rate':round(entries*100/eligible,2) if eligible else None,
            'wins':sum(value>0 for value in values),
            'hit_rate':round(sum(value>0 for value in values)*100/entries,2) if entries else None,
            'avg_return_pct':round(sum(values)/entries,4) if entries else None,
            'expected_return_pct':round(sum(values)/eligible,4) if eligible else None,
            'portfolio_return_pct':curve[-1]['value'] if curve else None,'curve':curve,
            'return_method':'每個訊號日配置相同本金、當日標的等額分配；總報酬只計已完成交易，以已實現損益除以已實現部位成本。'}


def _model_signals(report,model):
    return report.get('buy',[]) if model=='v1' else report.get('v4',{}).get('buy',[])


def _model_cohorts(reports,model,calendar,quotes,allowed_dates=None):
    cohorts=[]
    for report in reports:
        if allowed_dates is not None and report['date'] not in allowed_dates:continue
        lots=[]
        for signal in _model_signals(report,model):
            if model=='v1':
                outcome=signal.get('v1_strategy') or _evaluate_v1(signal,report['date'],calendar,quotes)
            elif model=='v4b':outcome=_evaluate_v4(signal,report['date'],'hybrid20',calendar,quotes)
            else:raise ValueError('未知的模型')
            if outcome.get('status')=='gap':continue
            lots.append({'symbol':signal['symbol'],'name':signal.get('name',signal['symbol']),'signal_date':report['date'],
                         'entry_date':outcome.get('entry_date'),
                         'entry_price':outcome.get('entry_price'),'exit_date':outcome.get('exit_date'),
                         'exit_price':outcome.get('exit_price'),'exit_return':outcome.get('net_return_pct'),
                         'status':outcome.get('status')})
        if lots:cohorts.append({'date':report['date'],'lots':lots})
    return cohorts


def _model_live_curve(reports,model,calendar,quotes,allowed_dates=None,cohorts=None):
    """Cost-weighted ROI across equal-budget signal-day cohorts, marked to each close."""
    cohorts=cohorts if cohorts is not None else _model_cohorts(reports,model,calendar,quotes,allowed_dates)
    if not cohorts or not calendar:return []
    start=min(cohort['date'] for cohort in cohorts);last_marks={};curve=[];previous_value=None
    for day in (value for value in calendar if start<=value):
        realized_profit=unrealized_profit=0.;realized_cost=unrealized_cost=0.
        active_cohorts=open_positions=closed_positions=pending_signals=0
        for cohort_index,cohort in enumerate(cohorts):
            if cohort['date']>day:continue
            active_cohorts+=1
            allocation=1/len(cohort['lots']) if cohort['lots'] else 0.
            for lot_index,lot in enumerate(cohort['lots']):
                entry_date=lot['entry_date'];entry_price=lot['entry_price'];exit_date=lot['exit_date']
                if not entry_date or not entry_price or day<entry_date:
                    pending_signals+=1;continue
                if exit_date and lot['exit_return'] is not None and day>=exit_date:
                    realized_cost+=allocation
                    realized_profit+=allocation*float(lot['exit_return'])/100
                    closed_positions+=1;continue
                quote=quotes.get((day,lot['symbol'])) or {};close=quote.get('close')
                mark_key=(cohort_index,lot_index)
                if isinstance(close,(int,float)) and math.isfinite(close) and close>0:last_marks[mark_key]=float(close)
                mark=last_marks.get(mark_key)
                unrealized_cost+=allocation
                if mark is not None:
                    unrealized_return=(mark/float(entry_price)-1)*100-ROUND_TRIP_PCT
                    unrealized_profit+=allocation*unrealized_return/100
                open_positions+=1
        invested_cost=realized_cost+unrealized_cost
        realized_value=realized_profit*100/realized_cost if realized_cost else None
        unrealized_value=unrealized_profit*100/unrealized_cost if unrealized_cost else 0.
        value=realized_value
        day_return=value-previous_value if value is not None and previous_value is not None else None
        curve.append({'date':day,'value':None if value is None else round(value,4),
                      'day_return_pct':None if day_return is None else round(day_return,4),
                      'realized_value':None if realized_value is None else round(realized_value,4),
                      'unrealized_value':round(unrealized_value,4),
                      'invested_cost_units':round(invested_cost,6),'realized_cost_units':round(realized_cost,6),
                      'unrealized_cost_units':round(unrealized_cost,6),
                      'eligible':active_cohorts,'entries':open_positions+closed_positions,
                      'open_positions':open_positions,'closed_positions':closed_positions,
                      'pending_signals':pending_signals})
        if value is not None:previous_value=value
    return curve


def _model_rankings(cohorts,calendar,quotes):
    if not cohorts or not calendar:return {'top_realized':[],'top_unrealized':[]}
    indices={day:index for index,day in enumerate(calendar)};latest=calendar[-1];realized=[];unrealized=[]
    for cohort in cohorts:
        for lot in cohort['lots']:
            entry_date=lot.get('entry_date');entry_price=lot.get('entry_price')
            if not entry_date or not entry_price or entry_date not in indices:continue
            base={'symbol':lot['symbol'],'name':lot['name'],'signal_date':lot['signal_date'],
                  'entry_date':entry_date,'entry_price':round(float(entry_price),4)}
            if lot.get('exit_date') and lot.get('exit_return') is not None and lot['exit_date'] in indices:
                realized.append({**base,'exit_date':lot['exit_date'],'exit_price':lot.get('exit_price'),
                                 'holding_sessions':indices[lot['exit_date']]-indices[entry_date]+1,
                                 'return_pct':round(float(lot['exit_return']),4)})
                continue
            mark=None
            for mark_date in reversed(calendar):
                if mark_date<entry_date or mark_date>latest:continue
                close=(quotes.get((mark_date,lot['symbol'])) or {}).get('close')
                if isinstance(close,(int,float)) and math.isfinite(close) and close>0:
                    mark=(mark_date,float(close));break
            if mark:
                unrealized.append({**base,'mark_date':mark[0],'mark_price':mark[1],
                                   'holding_sessions':indices[mark[0]]-indices[entry_date]+1,
                                   'return_pct':round((mark[1]/float(entry_price)-1)*100-ROUND_TRIP_PCT,4)})
    realized.sort(key=lambda row:(-row['return_pct'],row['symbol'],row['signal_date']))
    unrealized.sort(key=lambda row:(-row['return_pct'],row['symbol'],row['signal_date']))
    return {'top_realized':realized[:10],'top_unrealized':unrealized[:10]}


def _with_live_curve(result,reports,model,calendar,quotes,allowed_dates=None):
    cohorts=_model_cohorts(reports,model,calendar,quotes,allowed_dates)
    curve=_model_live_curve(reports,model,calendar,quotes,allowed_dates,cohorts);rankings=_model_rankings(cohorts,calendar,quotes)
    if not curve:return {**result,**rankings}
    lots=[lot for cohort in cohorts for lot in cohort['lots']]
    entered=[lot for lot in lots if lot.get('entry_date') and lot.get('entry_price')]
    closed=[lot for lot in entered if lot.get('exit_date') and lot.get('exit_return') is not None]
    unfilled=[lot for lot in lots if lot.get('status')=='unfilled']
    completed_returns=[float(lot['exit_return']) for lot in closed]
    decided=len(entered)+len(unfilled);wins=sum(value>0 for value in completed_returns)
    latest=curve[-1];return {**result,'curve':curve,'portfolio_return_pct':latest['value'],
        'realized_return_pct':latest['realized_value'],'unrealized_return_pct':latest['unrealized_value'],
        'report_days':len(cohorts),'eligible_signals':decided,'entries':len(entered),
        'fill_rate':round(len(entered)*100/decided,2) if decided else None,
        'wins':wins,'hit_rate':round(wins*100/len(closed),2) if closed else None,
        'avg_return_pct':round(sum(completed_returns)/len(completed_returns),4) if completed_returns else None,
        'as_of':latest['date'],'open_position_count':latest['open_positions'],
        'closed_position_count':latest['closed_positions'],'pending_signal_count':latest['pending_signals'],
        'valuation':'總報酬＝已實現損益÷已實現部位成本；未實現報酬＝未實現損益÷未實現部位成本，兩者分開計算。',
        **rankings}


def _bias20_rows(bars):
    result=[];window=deque()
    for source in sorted(bars,key=lambda row:row['date']):
        row=dict(source);close=row.get('close')
        if not isinstance(close,(int,float)) or not math.isfinite(close) or close<=0:continue
        window.append(float(close))
        if len(window)>20:window.popleft()
        row['bias20']=((close/(sum(window)/20))-1)*100 if len(window)==20 else None
        result.append(row)
    return result


def _v5_bias_result(tsmc_bars,asset_bars):
    """Treat every negative TSMC BIAS20 day as an equal-sized 00663L signal."""
    bias_rows=_bias20_rows(tsmc_bars);bias_by_date={row['date']:row['bias20'] for row in bias_rows}
    rows=[dict(row) for row in sorted(asset_bars,key=lambda row:row['date']) if row['date'] in bias_by_date]
    lots=[]
    for signal_index,row in enumerate(rows[:-1]):
        bias=bias_by_date.get(row['date'])
        if bias is None or bias>=V5_ENTRY_BIAS:continue
        entry_index=signal_index+1;entry_row=rows[entry_index];entry_price=entry_row.get('open')
        if not isinstance(entry_price,(int,float)) or not math.isfinite(entry_price) or entry_price<=0:continue
        lot={'symbol':'00663L','name':'國泰臺灣加權正2','signal_date':row['date'],
             'entry_date':entry_row['date'],'entry_price':float(entry_price),
             'entry_bias20':round(bias,4),'entry_index':entry_index}
        for exit_signal_index in range(entry_index,len(rows)-1):
            exit_bias=bias_by_date.get(rows[exit_signal_index]['date'])
            if exit_bias is None or exit_bias<V5_EXIT_BIAS:continue
            exit_row=rows[exit_signal_index+1];exit_price=exit_row.get('open')
            if not isinstance(exit_price,(int,float)) or not math.isfinite(exit_price) or exit_price<=0:continue
            gross=(float(exit_price)/lot['entry_price']-1)*100;net=gross-ETF_ROUND_TRIP_PCT
            lot.update(exit_signal_date=rows[exit_signal_index]['date'],exit_date=exit_row['date'],
                       exit_price=float(exit_price),exit_bias20=round(exit_bias,4),exit_index=exit_signal_index+1,
                       holding_sessions=exit_signal_index+1-entry_index,gross_return_pct=round(gross,4),
                       net_return_pct=round(net,4),hit=net>0)
            break
        lots.append(lot)
    trades=[lot for lot in lots if lot.get('exit_date')];latest=rows[-1] if rows else None;open_positions=[]
    if latest:
        for lot in lots:
            if lot.get('exit_date'):continue
            marked=(latest['close']/lot['entry_price']-1)*100-ETF_ROUND_TRIP_PCT
            open_positions.append({**lot,'mark_date':latest['date'],'mark_price':latest['close'],
                                   'holding_sessions':len(rows)-1-lot['entry_index'],
                                   'unrealized_return_pct':round(marked,4)})
    curve=[]
    for index,row in enumerate(rows):
        values=[]
        for lot in lots:
            if lot['entry_index']>index:continue
            if lot.get('exit_index') is not None and lot['exit_index']<=index:values.append(lot['net_return_pct'])
            elif lot.get('exit_index') is None or index<lot['exit_index']:
                values.append((row['close']/lot['entry_price']-1)*100-ETF_ROUND_TRIP_PCT)
        if values:curve.append({'date':row['date'],'value':round(sum(values)/len(values),4),
                                'day_return_pct':None,'eligible':len(values),'entries':len(values),
                                'open_positions':sum(lot.get('exit_index') is None or index<lot.get('exit_index',index+1) for lot in lots if lot['entry_index']<=index),
                                'closed_positions':sum(lot.get('exit_index') is not None and lot['exit_index']<=index for lot in lots)})
    realized=sum(lot['net_return_pct'] for lot in trades)/len(trades) if trades else 0.
    unrealized=sum(lot['unrealized_return_pct'] for lot in open_positions)/len(open_positions) if open_positions else 0.
    rankings=lambda values,field:[{'symbol':'00663L','name':'國泰臺灣加權正2','signal_date':lot['signal_date'],
        'entry_date':lot['entry_date'],'entry_price':lot['entry_price'],'exit_date':lot.get('exit_date'),
        'exit_price':lot.get('exit_price'),'mark_date':lot.get('mark_date'),'mark_price':lot.get('mark_price'),
        'holding_sessions':lot.get('holding_sessions',0),'return_pct':lot[field]}
        for lot in sorted(values,key=lambda item:-item[field])[:10]]
    current_bias=bias_by_date.get(latest['date']) if latest else None
    return {'key':'v5','label':'V5 00663L｜BIAS 6%','description':'台積電 BIAS20<0% 買入 00663L，BIAS20≥6% 賣出。',
            'hold_sessions':None,'report_days':len(rows),'eligible_signals':len(lots),'entries':len(lots),
            'fill_rate':100. if lots else None,'wins':sum(lot['hit'] for lot in trades),
            'hit_rate':round(sum(lot['hit'] for lot in trades)*100/len(trades),2) if trades else None,
            'avg_return_pct':round(realized,4) if trades else None,'expected_return_pct':round(realized,4) if trades else None,
            'portfolio_return_pct':curve[-1]['value'] if curve else None,'realized_return_pct':round(realized,4),
            'unrealized_return_pct':round(unrealized,4),'curve':curve,'entry_bias':V5_ENTRY_BIAS,'exit_bias':V5_EXIT_BIAS,
            'current_bias':None if current_bias is None else round(current_bias,2),'as_of':latest['date'] if latest else None,
            'trades':trades,'open_positions':open_positions,'open_position_count':len(open_positions),
            'closed_position_count':len(trades),'pending_signal_count':0,
            'top_realized':rankings(trades,'net_return_pct'),'top_unrealized':rankings(open_positions,'unrealized_return_pct'),
            'asset_symbol':'00663L','asset_name':'國泰臺灣加權正2','sell_rule':'bias6',
            'basis':'每個台積電 BIAS20<0% 日期建立一筆等額 00663L 部位；BIAS20≥6% 後次日開盤出場。'}


def _volatility_series(rows,window):
    returns=[];curve=[]
    previous=None
    for row in rows:
        close=row.get('close')
        if not isinstance(close,(int,float)) or not math.isfinite(close) or close<=0:continue
        if previous is not None:returns.append((row['date'],math.log(close/previous)))
        previous=close
        if len(returns)<window:continue
        sample=[value for _day,value in returns[-window:]];mean=sum(sample)/window
        volatility=math.sqrt(sum((value-mean)**2 for value in sample)/(window-1))*100
        curve.append({'date':row['date'],'value':round(volatility,2)})
    values=[row['value'] for row in curve];current=values[-1] if values else None
    return {'window':window,'current_pct':current,'as_of':curve[-1]['date'] if curve else None,
            'average_pct':round(sum(values)/len(values),2) if values else None,
            'min_pct':min(values) if values else None,'max_pct':max(values) if values else None,
            'percentile':round(sum(value<=current for value in values)*100/len(values),1) if values else None,'curve':curve}


def _market_volatility(index_bars,window=20,weekly_window=20):
    rows=sorted((dict(row) for row in index_bars),key=lambda row:row['date'])
    rows=[row for row in rows if isinstance(row.get('close'),(int,float)) and math.isfinite(row['close']) and row['close']>0]
    weekly={}
    for row in rows:
        parsed=date.fromisoformat(row['date']);iso=parsed.isocalendar();weekly[(iso.year,iso.week)]=row
    daily=_volatility_series(rows,window);weekly_result=_volatility_series(list(weekly.values()),weekly_window)
    current=daily['current_pct']
    if current is None:regime='資料不足'
    elif current<1:regime='低波動'
    elif current<1.6:regime='中度波動'
    else:regime='高波動'
    return {'daily':daily,'weekly':weekly_result,'regime':regime,
            # Kept for old saved clients; the current UI uses native daily and weekly volatility.
            'window':window,'annualized_pct':round(current*math.sqrt(252),2) if current is not None else None,
            'as_of':daily['as_of'],'average_pct':daily['average_pct'],'min_pct':daily['min_pct'],
            'max_pct':daily['max_pct'],'percentile':daily['percentile'],'curve':daily['curve'],
            'method':'日：近 20 個交易日每日對數報酬標準差；周：每周最後交易日收盤的近 20 周對數報酬標準差（均未年化）'}


def _model_performance(reports,calendar,quotes,tsmc_bars,leveraged_bars,index_bars,market_filter):
    models = {'v5': _v5_bias_result(tsmc_bars,leveraged_bars)}
    return {'models':models,'comparison':dict(models),'common_dates':[],
            'mature_common_dates':[], 'market_volatility':_market_volatility(index_bars),
            'comparison_note':'V5 為台積電 BIAS 的 00663L 事件，依事件與部位成本計算。'}


def _summary(reports):
    result={}
    for horizon in map(str,BACKTEST_HORIZONS):
        result[horizon]={}
        for side in ('buy','sell','combined'):
            per_report={};values=[]
            for report in reports:
                signals=report['buy']+report['sell'] if side=='combined' else report[side]
                day_values=[]
                for signal in signals:
                    value=signal.get('backtest',{}).get('horizons',{}).get(horizon,{})
                    if value.get('status')=='scored':values.append(value['net_return_pct']);day_values.append(value['net_return_pct'])
                if day_values:per_report[report['date']]=sum(day_values)/len(day_values)
            portfolio=sum(per_report.values())/len(per_report) if per_report else None
            result[horizon][side]={'samples':len(values),'wins':sum(v>0 for v in values),
                'hit_rate':round(sum(v>0 for v in values)*100/len(values),2) if values else None,
                'avg_return_pct':round(sum(values)/len(values),4) if values else None,
                'portfolio_return_pct':round(portfolio,4) if portfolio is not None else None,
                'report_days':len(per_report)}
    return result


def _attach_strategy_history(reports):
    """Attach point-in-time win/loss counts without changing frozen selections."""
    events=defaultdict(list)
    for report in reports:
        signal_date=report['date']
        for signal in report.get('buy',[]):
            outcome=signal.get('v1_strategy',{})
            if outcome.get('status')=='scored' and outcome.get('exit_date') and outcome.get('hit') is not None:
                events[('v1',signal['symbol'])].append((signal_date,outcome['exit_date'],bool(outcome['hit']),outcome.get('net_return_pct')))
        for signal in report.get('sell',[]):
            outcome=signal.get('backtest',{}).get('horizons',{}).get(str(V4_HOLD),{})
            if outcome.get('status')=='scored' and outcome.get('exit_date') and outcome.get('hit') is not None:
                events[('v1_sell',signal['symbol'])].append((signal_date,outcome['exit_date'],bool(outcome['hit']),outcome.get('net_return_pct')))
        for signal in report.get('v4',{}).get('buy',[]):
            for model,strategy in (('v4b','hybrid20'),):
                outcome=signal.get('strategies',{}).get(strategy,{})
                if outcome.get('status')=='scored' and outcome.get('exit_date') and outcome.get('hit') is not None:
                    events[(model,signal['symbol'])].append((signal_date,outcome['exit_date'],bool(outcome['hit']),outcome.get('net_return_pct')))
        for signal in report.get('v4',{}).get('sell',[]):
            outcome=signal.get('risk_backtest',{}).get('horizons',{}).get(str(V4_HOLD),{})
            if outcome.get('status')=='scored' and outcome.get('exit_date') and outcome.get('hit') is not None:
                events[('v4_sell',signal['symbol'])].append((signal_date,outcome['exit_date'],bool(outcome['hit']),outcome.get('net_return_pct')))

    def history(model,symbol,as_of):
        completed=[event for event in events.get((model,symbol),[]) if event[0]<as_of and event[1]<=as_of]
        wins=sum(event[2] for event in completed);samples=len(completed)
        latest=max(completed,key=lambda event:(event[1],event[0])) if completed else None
        return {'wins':wins,'losses':samples-wins,'samples':samples,
                'win_rate':round(wins*100/samples,1) if samples else None,
                'last_result':'win' if latest and latest[2] else 'loss' if latest else None,
                'last_return_pct':latest[3] if latest else None,
                'last_exit_date':latest[1] if latest else None}

    for report in reports:
        as_of=report['date']
        for signal in report.get('buy',[]):
            signal.setdefault('strategy_history',{})['v1']=history('v1',signal['symbol'],as_of)
        for signal in report.get('sell',[]):
            signal.setdefault('strategy_history',{})['v1_sell']=history('v1_sell',signal['symbol'],as_of)
        for signal in report.get('v4',{}).get('buy',[]):
            signal.setdefault('strategy_history',{}).update({
                'v4b':history('v4b',signal['symbol'],as_of),
            })
        for signal in report.get('v4',{}).get('sell',[]):
            signal.setdefault('strategy_history',{})['v4_sell']=history('v4_sell',signal['symbol'],as_of)
    return reports


def report(day=None):
    dates=available_dates()
    with db.connect() as c:
        stored=[dict(r) for r in c.execute('SELECT * FROM daily_follow_reports ORDER BY date DESC')]
        calendar=[r[0] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' ORDER BY date")]
        tsmc_bars=[dict(r) for r in c.execute("SELECT date,open,high,low,close FROM prices WHERE symbol='2330' ORDER BY date")]
        leveraged_bars=[dict(r) for r in c.execute("SELECT date,open,high,low,close FROM prices WHERE symbol='00663L' ORDER BY date")]
        index_bars=[dict(r) for r in c.execute("SELECT date,close FROM prices WHERE symbol='TAIEX' ORDER BY date")]
        tpex_index_bars=[dict(r) for r in c.execute("SELECT date,close FROM prices WHERE symbol='TPEX' ORDER BY date")]
        if stored:
            first=min(r['date'] for r in stored)
            quotes={(r['date'],r['symbol']):dict(r) for r in c.execute('SELECT date,symbol,open,high,low,close FROM prices WHERE date>=? ORDER BY date',(first,))}
        else:quotes={}
    market_filter=_dual_index_sma20_filter(index_bars,tpex_index_bars)
    # Read archived lists without rewriting them or valuing retired V1/V4 models.
    reports=[json.loads(row['payload']) for row in stored]
    saved_dates=[r['date'] for r in reports]
    if day and day not in saved_dates:raise ValueError('此日期尚未建立跟單名單')
    selected=next((r for r in reports if r['date']==day),reports[0] if reports else None)
    from . import v1, v2, v3, v4, v8, v9_tx, v10, v11
    v10_result=v10.report()  # Saved manual clone only; GET never recomputes it.
    v11_result=v11.report()  # Manual alpha account: saved state only, never generate or value.
    v1_result=v1.report()
    v2_result=v2.report()
    v3_result=v3.report()  # Read saved manual account only; never refresh or generate.
    v4_result=v4.report()
    v8_result=v8.report()
    try:v9_result=v9_tx.report()
    except (OSError,ValueError,sqlite3.Error) as error:
        v9_result={'key':'v9','label':'V9 台指期回檔反手','status':'failed','error':str(error),
                   'portfolio_return_pct':None,'curve':[],'years':[]}
    performance=_model_performance(reports,calendar,quotes,tsmc_bars,leveraged_bars,index_bars,market_filter)
    performance['models']['v1']=v1_result
    performance['comparison']['v1']=v1_result
    performance['models']['v2']=v2_result
    performance['comparison']['v2']=v2_result
    performance['models']['v3']=v3_result
    performance['comparison']['v3']=v3_result
    performance['models']['v4']=v4_result
    performance['comparison']['v4']=v4_result
    performance['models']['v8']=v8_result
    performance['comparison']['v8']=v8_result
    performance['models']['v9']=v9_result
    performance['comparison']['v9']=v9_result
    performance['models']['v10']=v10_result
    performance['comparison']['v10']=v10_result
    performance['models']['v11']=v11_result
    performance['comparison']['v11']=v11_result
    model_order=['v1','v10','v11','v2','v3','v4','v5','v8','v9']
    for field in ('models','comparison'):
        performance[field]={key:performance[field][key] for key in model_order}
    performance['model_order']=model_order
    # Display-only calendar for per-stock completed-trade holding durations.
    # It never changes any model's saved account or selected trading rules.
    performance['trading_calendar']=calendar
    # Derive display-only aggregates from full closed-trade records. In
    # particular, manual models remain frozen and are never published here.
    performance['holding_duration']={
        key:holding_duration.summarize(model.get('trades',[]),calendar,as_of=model.get('as_of'))
        for key,model in performance['models'].items()
    }
    performance['comparison_note']+=' V11 是100萬元獨立手動 α 趨勢帳戶，只讀取對話保存結果；歷史研究報酬不併入帳戶。'
    performance['comparison_note']+=' V1、V2、V8 採固定 100 萬本金帳戶；V4 從 2026-10-16 以十萬元空手起跑，最多 1.5 倍槓桿並扣借款利息；V9 顯示台指期當年一口名目點數報酬。V6 已移至獨立的股災氣象球頁。V3 為10萬元手動帳戶，自建立日起記錄，歷史研究另列。不同口徑及起始日不可直接當作同額本金排名。'
    return {'available':bool(selected),'selected':selected,'reports':reports,'saved_dates':saved_dates,
            'eligible_dates':dates,'summary':{},
            'model_performance':performance,'v11':v11_result,'v10':v10_result,'v1':v1_result,'v2':v2_result,'v3':v3_result,'v4':v4_result,'v8':v8_result,'v9':v9_result,
            'v4_method':V4_METHOD,'v5_method':V5_METHOD,
            'v1_method':v1_result.get('description','V1 獲利保護優化'),'method':METHOD,'version':VERSION,
            'round_trip_cost_pct':round(ROUND_TRIP_PCT,4)}
