"""Read-only leaderboard of tracked branches using the existing FIFO net-flow model."""
from __future__ import annotations
import json
import math
import threading
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from . import db
from .analytics import analyze

STYLES=[('day','0 日當沖',0,0),('overnight','1 日隔日沖',1,1),('quick','2–4 日極短線',2,4),('short','5–20 日短線',5,20),('swing','21 日以上波段',21,None)]
_lock=threading.Lock()

def now():return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')

def forward_stats(flows, price_map, calendar, as_of, bad_calendar=()):
    """Equal-weight gross close returns for each positive-net stock/day signal."""
    days=sorted(set(d for d in calendar if d<=as_of))
    indices={d:i for i,d in enumerate(days)}
    buys={(r['date'],r['symbol']) for r in flows if r['date']<=as_of and r['net']>0}
    invalid=set(d for d in bad_calendar if d<=as_of)
    result={}
    for horizon in (5,10):
        returns=[];pending=missing=0
        for day,symbol in buys:
            index=indices.get(day)
            if index is None:
                missing+=1;continue
            end=days[index+horizon] if index+horizon<len(days) else None
            if any(day<=d<=(end or as_of) for d in invalid):
                missing+=1;continue
            if end is None:
                pending+=1;continue
            start_price=price_map.get((day,symbol));end_price=price_map.get((end,symbol))
            if not all(p is not None and math.isfinite(p) and p>0 for p in (start_price,end_price)):
                missing+=1;continue
            returns.append((end_price/start_price-1)*100)
        count=len(returns);wins=sum(r>1e-7 for r in returns)
        result[str(horizon)]=dict(win_rate=wins/count*100 if count else None,
            avg_return=sum(returns)/count if count else None,wins=wins,samples=count,
            pending=pending,missing=missing,total=len(buys))
    return result

def style_key(days):
    if days is None:return 'unknown'
    for key,_,low,high in STYLES:
        if days>=low and (high is None or days<=high):return key
    return 'unknown'

def style_stats(cycles):
    buckets={key:[] for key,_,_,_ in STYLES};buckets['unknown']=[]
    valid=[r for r in cycles if r['complete']]
    for row in valid:buckets[style_key(row['holding_days'])].append(row)
    result=[]
    for key,label,_,_ in [*STYLES,('unknown','天數待確認',None,None)]:
        rows=buckets[key];count=len(rows);wins=sum(r['pnl']>1e-7 for r in rows)
        # Daily net aggregates do not preserve intraday buy/sell order or price.
        observable=key!='day'
        result.append(dict(key=key,label=label,observable=observable,count=count if observable else None,
            wins=wins if observable else None,win_rate=wins/count*100 if count and observable else None,
            pnl=sum(r['pnl'] for r in rows) if count and observable else None,
            gross_profit=sum(max(0,r['pnl']) for r in rows) if count and observable else None,
            gross_loss=sum(min(0,r['pnl']) for r in rows) if count and observable else None,
            mean_days=sum(r['holding_days'] for r in rows)/count if count and observable and key!='unknown' else None,
            share_pct=count/len(valid)*100 if valid and observable else None))
    eligible=[r for r in result if r['observable'] and r['key']!='unknown' and r['count']]
    dominant=max(eligible,key=lambda r:(r['count'],r['pnl'])) if eligible else None
    profitable=[r for r in eligible if r['pnl']>1e-7]
    profit_style=max(profitable,key=lambda r:(r['pnl'],r['count'])) if profitable else None
    return result,dominant['label'] if dominant else None,profit_style['label'] if profit_style else None

def summarize(branch,result,flows,price_map,first,as_of,observed,verified,candidates):
    styles,dominant,profit_style=style_stats(result['cycles'])
    scored=[r for r in result['cycles'] if r['complete']]
    pnl=sum(r['pnl'] for r in scored) if scored else None
    gross=0.;missing=0;buy=sell=0.
    for row in flows:
        quantity=row['buy']+row['sell']
        if quantity<=0:continue
        price=price_map.get((row['date'],row['symbol']))
        if price is None:missing+=1;continue
        buy+=row['buy']*1000*price;sell+=row['sell']*1000*price
    gross=buy+sell
    stats=result['stats'];cycle=result['cycle_stats']
    return dict(branch,first_date=first,as_of=as_of,observed=observed,
        status='ready' if observed and verified==candidates else 'partial' if flows else 'missing',
        verified_stocks=verified,candidate_stocks=candidates,
        paired_rate=stats['win_rate'],paired_wins=stats['wins'],paired_samples=stats['closed_count'],paired_unscored=stats['unscored_events'],
        cycle_rate=cycle['win_rate'],cycle_wins=cycle['wins'],cycle_samples=cycle['closed_count'],cycle_unscored=cycle['unscored_count'],open_cycles=cycle['open_count'],
        turnover=gross if flows and not missing else None,known_turnover=gross if flows else None,
        buy_amount=buy if flows and not missing else None,sell_amount=sell if flows and not missing else None,unpriced_flows=missing,
        cycle_pnl=pnl,cycle_gross_profit=sum(max(0,r['pnl']) for r in scored) if scored else None,
        cycle_gross_loss=sum(min(0,r['pnl']) for r in scored) if scored else None,
        dominant_style=dominant,profit_style=profit_style,styles=styles)

def report(as_of=None,refresh=False):
    # The lock prevents overlapping full-portfolio calculations on repeated clicks.
    with _lock:
        ignored=db.ignored_symbols()
        with db.connect() as c:
            c.execute('BEGIN')
            branches=[dict(r) for r in c.execute('SELECT * FROM branches WHERE active=1 ORDER BY broker,branch')]
            dates=[r[0] for r in c.execute("SELECT DISTINCT s.date FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed' ORDER BY s.date DESC")]
            if as_of and as_of not in dates:raise ValueError('此日期沒有已追蹤分點資料')
            # A saved report belongs to its watchlist and calculation policy.
            # New market data alone does not replace a manually saved report.
            signature=json.dumps([1,sorted(ignored),[(b['broker'],b['branch'],b['name']) for b in branches]],ensure_ascii=False)
            if not refresh:
                saved=c.execute('SELECT payload FROM leader_reports WHERE scope=?'+(' AND date=?' if as_of else '')+' ORDER BY date DESC LIMIT 1',
                                (signature,as_of) if as_of else (signature,)).fetchone()
                if saved:
                    payload=json.loads(saved['payload'])
                    payload.update(available_dates=dates,source_latest_date=dates[0] if dates else None)
                    return payload
            as_of=as_of or (dates[0] if dates else None)
            flows=[dict(r) for r in c.execute('SELECT f.* FROM flows f JOIN branches b USING(broker,branch) WHERE b.active=1 AND f.date<=? ORDER BY f.date,f.symbol',(as_of or '',)) if r['symbol'] not in ignored]
            first=min((r['date'] for r in flows),default=as_of or '9999-12-31')
            prices=[dict(r) for r in c.execute('SELECT * FROM prices WHERE date>=? AND date<=?',(first,as_of or ''))]
            calendar=[r['date'] for r in c.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date>=? AND date<=? ORDER BY date",(first,as_of or ''))]
            bad_calendar=[r['date'] for r in c.execute("SELECT date FROM market_days WHERE twse_status='error' AND date>=? AND date<=?",(first,as_of or ''))]
            intervals=[dict(r) for r in c.execute('SELECT h.* FROM history_intervals h JOIN branches b USING(broker,branch) WHERE b.active=1') if r['symbol'] not in ignored]
            candidate_rows=[dict(r) for r in c.execute('SELECT x.* FROM candidates x JOIN branches b USING(broker,branch) WHERE b.active=1') if r['symbol'] not in ignored]
            observed={(r['broker'],r['branch']) for r in c.execute("SELECT broker,branch FROM snapshots WHERE date=? AND status='observed'",(as_of,))}
            job=c.execute('SELECT status,message FROM jobs ORDER BY started_at DESC LIMIT 1').fetchone()
        by_branch=defaultdict(list);coverage=defaultdict(list);candidates=defaultdict(set)
        for row in flows:by_branch[(row['broker'],row['branch'])].append(row)
        for row in intervals:coverage[(row['broker'],row['branch'])].append(row)
        for row in candidate_rows:candidates[(row['broker'],row['branch'])].add(row['symbol'])
        prices_by_key={(r['date'],r['symbol']):r['close'] for r in prices};rows=[]
        for branch in branches:
            key=(branch['broker'],branch['branch']);branch_flows=by_branch[key]
            start=min((r['date'] for r in branch_flows),default=as_of or '9999-12-31')
            full={r['symbol'] for r in coverage[key] if r['start']<=start and r['end']>=(as_of or '')}
            symbols={r['symbol'] for r in branch_flows}
            relevant=[r for r in prices if r['symbol'] in symbols or r['symbol']=='TAIEX']
            result=analyze(branch_flows,relevant,[d for d in calendar if d>=start],as_of or start,
                not any(d>=start for d in bad_calendar),full,coverage[key])
            row=summarize(branch,result,branch_flows,prices_by_key,start if branch_flows else None,as_of,key in observed,
                          len(full&candidates[key]),len(candidates[key]))
            row['forward']=forward_stats(branch_flows,prices_by_key,calendar,as_of or start,bad_calendar)
            for horizon,stats in row['forward'].items():
                row['forward_'+horizon+'_rate']=stats['win_rate']
                row['forward_'+horizon+'_return']=stats['avg_return']
                row['forward_'+horizon+'_samples']=stats['samples']
            rows.append(row)
        payload=dict(as_of=as_of,available_dates=dates,computed_at=now(),rows=rows,job=dict(job) if job else None,
            totals=dict(branches=len(rows),ready=sum(r['status']=='ready' for r in rows),paired_samples=sum(r['paired_samples'] for r in rows),
                        cycle_samples=sum(r['cycle_samples'] for r in rows),unscored_cycles=sum(r['cycle_unscored'] for r in rows)))
        payload['source_latest_date']=dates[0] if dates else None
        if as_of:
            # Commit only a complete calculation; a failed refresh keeps the
            # prior report, including its original date and computed_at value.
            with db.connect() as c:
                c.execute('INSERT OR REPLACE INTO leader_reports VALUES (?,?,?)',
                          (signature,as_of,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
        return payload
