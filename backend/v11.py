"""V11 alpha + trend manual paper account. Only CLI publish can change state.

There is no HTTP publication route or scheduler hook. Reconciliation consumes
only previously saved orders and trailing-stop rules, never retrospective ranks.
"""
from __future__ import annotations
import argparse,copy,fcntl,gzip,hashlib,json,math,os,tempfile
from collections import defaultdict
from datetime import date,datetime,time,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from . import db,alpha_beta as ab,price_levels,v2

VERSION='v11-manual-alpha-trend-20261029'
LABEL='V11 手動 α 趨勢'
INITIAL=1_000_000.
TZ=ZoneInfo('Asia/Taipei')
POLICY='僅依對話要求，與 V3、V6、V10 一起手動更新；前台一般更新、讀取與重新整理均只顯示已保存紀錄。'
METHOD='上市櫃共用加權含息基準，120 日正 α 依 HAC(5) t 值排序；加入 20MA 轉強與近 20 日正超額，最多三檔，不加槓桿。'
EXECUTION='獨立模擬帳戶；只有手動保存的買入計畫可在保存之後的開盤試算。兩次更新間不新增選股；既定 3ATR／8% 防線及達 +8% 後 2ATR 追蹤，於下次手動更新核對。'

def _store(store=None):return Path(store) if store is not None else db.DATA/'v11_manual'
def _rows(store):
    result=[]
    if not store.exists():return result
    for p in sorted(store.glob('[0-9]*.json'),reverse=True):
        row=json.loads(p.read_text())
        if row.get('version')!=VERSION or not isinstance(row.get('model'),dict):raise ValueError('V11 快照版本不符，保留原紀錄')
        result.append(row)
    return result
def _empty():
    return dict(key='v11',label=LABEL,version=VERSION,mode='manual',available=False,revision=0,
        initial=INITIAL,cash=INITIAL,equity=INITIAL,dividend_receivable=0.,start=None,as_of=None,created_at=None,
        recorded_at=None,holdings=[],open_positions=[],pending_orders=[],trades=[],order_events=[],
        curve=[],days={},activity=[],realized_pnl=0.,unrealized_pnl=0.,portfolio_return_pct=0.,
        max_drawdown_pct=0.,hit_rate=None,wins=0,entries=0,review=None,update_policy=POLICY,method=METHOD,execution_note=EXECUTION)
def report(store=None):
    rows=_rows(_store(store))
    result=copy.deepcopy(rows[0]['model']) if rows else _empty()
    result['snapshot_history']=[{k:r[k] for k in ['revision','recorded_at','as_of']} for r in rows]
    return result
def load_inputs(path=None):
    """Read exact saved official input; does not fetch, publish alpha or touch DB."""
    p=Path(path) if path else db.DATA/'alpha_beta/inputs.json.gz'
    encoded=gzip.decompress(p.read_bytes());value=json.loads(encoded)
    digest=hashlib.sha256(encoded).hexdigest()
    if path is None:
        latest=json.loads((db.DATA/'alpha_beta/latest.json').read_text())
        if latest.get('input_sha256')!=digest or latest.get('as_of')!=value.get('as_of'):raise ValueError('Alpha 原始資料與已保存版本不一致，請先完成資料更新')
    return value,digest,encoded
def _finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def _date(d):
    if not isinstance(d,str) or date.fromisoformat(d).isoformat()!=d:raise ValueError('日期必須為 YYYY-MM-DD')
    return d
def _bars(inputs):
    by=defaultdict(dict)
    for p in inputs['prices']:
        if p['date']<=inputs['as_of']:by[p['symbol']][p['date']]=p
    return by
def _atr(pp,day,calendar):
    dd=[d for d in calendar if d<=day][-15:]
    if len(dd)<15 or any(d not in pp for d in dd):return None
    tr=[]
    for a,b in zip(dd,dd[1:]):
        p=pp[b];cp=pp[a]['close'];h=p.get('high');l=p.get('low')
        if not all(_finite(x) for x in [cp,h,l]) or h<l:return None
        tr.append(max(h-l,abs(h-cp),abs(l-cp)))
    return sum(tr)/14
def context(inputs,now):
    asof=_date(inputs['as_of']);dates=sorted(set(d for d in inputs['dates'] if d<=asof));pp=_bars(inputs)
    observed=inputs.get('disposition_observed_at')
    if not isinstance(observed,str) or observed[:10]!=now.date().isoformat():raise ValueError('V11 處置資料不是本次發布日，請先更新官方原始資料')
    if len(dates)<121 or dates[-1]!=asof:raise ValueError('V11 需要完整 120 日報酬與最新收盤資料')
    dd=dates[-121:];levels=inputs['benchmarks']['TAIEX_TR']
    if any(d not in levels for d in dd):raise ValueError('加權含息報酬指數有缺日')
    x=np.array([levels[b]/levels[a]-1 for a,b in zip(dd,dd[1:])]);indices={}
    for s in ['TAIEX','TPEX']:
        closes=[pp.get(s,{}).get(d,{}).get('close') for d in dates[-20:]]
        if len(closes)!=20 or not all(_finite(c) and c>0 for c in closes):raise ValueError('加權或櫃買 20MA 資料不足，停止新增決策')
        ma=sum(closes)/20;indices[s]=dict(close=closes[-1],ma20=ma,above=closes[-1]>=ma)
    blocked=all(not r['above'] for r in indices.values())
    ev=defaultdict(dict)
    for e in inputs['events']:ev[e['symbol']][e['date']]=e
    unsafe=defaultdict(set)
    for e in inputs['unsafe']:unsafe[e['symbol']].add(e['date'])
    amounts={(r['date'],r['market']):r['values'] for r in inputs['amounts']}
    candidates=[];counts=dict(ordinary=0,valid_alpha=0,qualified=0,disposition_excluded=0)
    for symbol,series in sorted(pp.items()):
        if not (len(symbol)==4 and symbol.isdigit() and not symbol.startswith('0')):continue
        counts['ordinary']+=1;p=series.get(asof,{})
        if not p or p['close']<10:continue
        values=[amounts.get((d,p['market']),{}).get(symbol) for d in dates[-20:]]
        if not all(_finite(v) and v>=0 for v in values) or sum(values)/20<20e6:continue
        if any(dd[0]<d<=asof for d in unsafe[symbol]):continue
        try:y=np.array(ab.total_returns(series,dd,ev[symbol]));fit=ab.fit_market(y,x)
        except (ValueError,KeyError,ZeroDivisionError):continue
        if fit['sigma']<ab.PROTOCOL['volatility_floor'] or not _finite(fit['alpha_t']):continue
        counts['valid_alpha']+=1
        closes=[series[d]['close'] for d in dd];ma20=sum(closes[-20:])/20;prior_ma=sum(closes[-25:-5])/20
        excess=float(np.sum(y[-20:]-fit['beta']*x[-20:]))
        if not (fit['intercept']>0 and p['close']>ma20 and ma20>prior_ma and excess>0 and p['close']/ma20<=1.20):continue
        atr=_atr(series,asof,dates)
        if atr is None or atr<=0:continue
        disposition=ab.disposition_at(inputs,symbol,p['market'],now.date().isoformat(),asof)
        if disposition['excluded_by_default']:counts['disposition_excluded']+=1;continue
        highs=np.array([[series.get(d,{}).get('high',np.nan)] for d in dd],float)
        resistance=price_levels.first_resistance(highs,p['close'],atr,len(dd)-1,0)
        flags=[]
        if fit['r2'] is not None and fit['r2']<.1:flags.append('市場解釋度低，β 僅供參考')
        if disposition['upcoming']:flags.append('已公告即將處置')
        candidates.append(dict(symbol=symbol,name=p['name'],close=p['close'],alpha_pct=fit['intercept']*100,
            alpha_t=fit['alpha_t'],alpha_ci_pct=[v*100 for v in fit['alpha_ci']],beta=fit['beta'],r2=fit['r2'],
            excess20_pct=excess*100,avg_amount=sum(values)/20,ma20=ma20,atr=atr,prior_volume=p['volume'],
            initial_stop_ref=v2.tick_floor(max(p['close']-3*atr,p['close']*.92)),resistance=resistance,
            disposition=disposition,flags=flags))
    candidates.sort(key=lambda r:(-r['alpha_t'],r['symbol']));counts['qualified']=len(candidates)
    return dict(as_of=asof,indices=indices,blocked=blocked,candidates=candidates[:3],coverage=counts,
                benchmark='TAIEX_TR',alpha_method=ab.VERSION,price_date=asof,
                disposition_observed_at=inputs.get('disposition_observed_at'))

def _fillable(b):return bool(b and all(_finite(b.get(k)) for k in ['open','high','low','volume']) and b['open']>0 and b['high']>b['low'] and b['volume']>0)
def _mark(state,day,by,calendar):
    for pos in state['holdings']:
        bar=by.get(pos['symbol'],{}).get(day,{})
        cp=bar.get('close')
        if _finite(cp) and cp>0:pos.update(mark_price=cp,mark_date=day,peak=max(pos['peak'],cp))
        pos['value']=pos['qty']*pos['mark_price']*(1-v2.SELL)
        pos['pnl']=pos['value']+pos.get('dividend',0)-pos['cost'];pos['return_pct']=pos['pnl']/pos['cost']*100
        pos['holding_sessions']=len([d for d in calendar if pos['entry_date']<d<=day])
        atr=_atr(by.get(pos['symbol'],{}),day,calendar)
        pos['protection_active']=bool(pos.get('protection_active') or pos['peak']>=pos['defense_entry']*1.08)
        multiple=2 if pos['protection_active'] else 3
        pos['next_stop']=v2.tick_floor(max(pos['stop'],pos['peak']-multiple*atr)) if atr is not None else pos['stop']
    state['realized_pnl']=sum(t['pnl'] for t in state['trades']);state['unrealized_pnl']=sum(p['pnl'] for p in state['holdings'])
    state['equity']=state['cash']+state['dividend_receivable']+sum(p['value'] for p in state['holdings'])
    if state['cash']<-.001 or abs(state['equity']-(INITIAL+state['realized_pnl']+state['unrealized_pnl']))>.001:raise ValueError('V11 帳戶金額不平衡，停止寫入')
    state['portfolio_return_pct']=(state['equity']/INITIAL-1)*100;state['as_of']=day
    point=dict(date=day,value=state['portfolio_return_pct'],equity=state['equity'],cash=state['cash'],open_positions=len(state['holdings']),closed_positions=len(state['trades']))
    if state['curve'] and state['curve'][-1]['date']==day:state['curve'][-1]=point
    else:state['curve'].append(point)
    values=np.array([p['equity'] for p in state['curve']]);state['max_drawdown_pct']=float(np.min(values/np.maximum.accumulate(values)-1)*100)
    state['wins']=sum(t['pnl']>0 for t in state['trades']);state['hit_rate']=state['wins']/len(state['trades'])*100 if state['trades'] else None
    state['entries']=len(state['trades'])+len(state['holdings']);state['open_positions']=copy.deepcopy(state['holdings'])
def _sell(state,pos,day,price,reason,calendar):
    value=pos['qty']*price*(1-v2.SELL);state['cash']+=value
    pnl=value+pos.get('dividend',0)-pos['cost']
    state['trades'].append(dict(pos,exit_date=day,exit_price=price,pnl=pnl,return_pct=pnl/pos['cost']*100,reason=reason,
                               holding_sessions=sum(pos['entry_date']<d<=day for d in calendar)))
    state['holdings'].remove(pos)
def reconcile(state,inputs):
    state=copy.deepcopy(state);by=_bars(inputs);calendar=sorted(d for d in inputs['dates'] if d<=inputs['as_of'])
    if state['as_of'] and state['as_of']<calendar[0]:raise ValueError('V11 核對行情無法銜接上次保存日，請補齊歷史資料')
    ev={(e['date'],e['symbol']):e for e in inputs['events']};unsafe={(e['date'],e['symbol']) for e in inputs['unsafe']}
    for day in calendar:
        if not state['as_of'] or day<=state['as_of']:continue
        buys=[];sells=[];exited=set()
        for pos in list(state['holdings']):
            s=pos['symbol'];e=ev.get((day,s));bar=by.get(s,{}).get(day,{})
            if (day,s) in unsafe or (e and e['unsafe']):raise ValueError(f'V11 持股 {s} 在 {day} 有未還原公司事件，保留原快照，請先核對')
            pos['stop']=pos.get('next_stop',pos['stop'])
            if e and e.get('cash'):
                cash=e['cash'];state['dividend_receivable']+=pos['qty']*cash;pos['dividend']=pos.get('dividend',0)+pos['qty']*cash
                for field in ['stop','peak','mark_price','defense_entry']:pos[field]=max(.01,pos[field]-cash)
            if pos.get('pending_exit') or (_finite(bar.get('open')) and bar['open']<=pos['stop']):
                if _fillable(bar):_sell(state,pos,day,bar['open'],'既定防線／跳空退出',calendar);sells.append(s);exited.add(s)
                else:pos['pending_exit']=True
        for order in list(state['pending_orders']):
            if day<=order['not_before_date']:continue
            state['pending_orders'].remove(order);s=order['symbol'];bar=by.get(s,{}).get(day,{})
            if len(state['holdings'])>=3 or s in exited or any(p['symbol']==s for p in state['holdings']) or not _fillable(bar):
                state['order_events'].append(dict(order,date=day,status='cancelled',reason='無空位、當日剛退出或開盤無法成交'));continue
            # A new position on an exdate has no entitlement to that dividend.
            equity=state['cash']+state['dividend_receivable']+sum(p['qty']*p['mark_price']*(1-v2.SELL) for p in state['holdings'])
            budget=min(state['cash'],order['budget'],equity/3);op=bar['open'];qty=min(int(budget/(op*(1+v2.BUY))),order['max_qty'])
            if qty<1:state['order_events'].append(dict(order,date=day,status='cancelled',reason='現金或可買股數不足'));continue
            cost=qty*op*(1+v2.BUY);state['cash']-=cost;stop=v2.tick_floor(max(op-3*order['atr'],op*.92))
            state['holdings'].append(dict(symbol=s,name=order['name'],qty=qty,cost=cost,entry_date=day,entry_price=op,
                signal_date=order['signal_date'],mark_price=op,mark_date=day,peak=op,defense_entry=op,stop=stop,next_stop=stop,
                dividend=0.,protection_active=False,pending_exit=False))
            state['order_events'].append(dict(order,date=day,status='filled',qty=qty,price=op));buys.append(s)
        for pos in list(state['holdings']):
            bar=by.get(pos['symbol'],{}).get(day,{})
            if _finite(bar.get('low')) and bar['low']<=pos['stop']:
                if _fillable(bar):_sell(state,pos,day,min(bar['open'],pos['stop']),'盤中既定停損',calendar);sells.append(pos['symbol'])
                else:pos['pending_exit']=True
        _mark(state,day,by,calendar)
        state['days'][day]=dict(candidates=[],holdings=copy.deepcopy(state['holdings']),buy=buys,sell=sells,cash=state['cash'],equity=state['equity'],kind='核對既定計畫')
        state['activity'].append(dict(date=day,buy=buys,sell=sells,hold=[p['symbol'] for p in state['holdings']]))
    return state

def publish(decision,*,inputs_path=None,store=None,now=None):
    if not isinstance(decision,dict):raise ValueError('V11 更新需提供研究紀錄')
    asof=_date(decision.get('as_of'));revision=decision.get('expected_revision');analysis=decision.get('analysis')
    if isinstance(revision,bool) or not isinstance(revision,int) or revision<0:raise ValueError('V11 需提供有效版號')
    if not isinstance(analysis,str) or len(analysis.strip())<10:raise ValueError('V11 請附本次研究與資料說明')
    sources=decision.get('sources',[])
    if not isinstance(sources,list) or any(not isinstance(s,dict) or not isinstance(s.get('title'),str) or not isinstance(s.get('url'),str) or not s['url'].startswith('https://') for s in sources):raise ValueError('V11 來源需附標題與 HTTPS 網址')
    stamp=now or datetime.now(TZ)
    if stamp.tzinfo is None:raise ValueError('V11 保存時間需含時區')
    stamp=stamp.astimezone(TZ)
    if asof>stamp.date().isoformat() or (asof==stamp.date().isoformat() and stamp.time()<time(16)):raise ValueError('V11 只能使用已完成且可取得的收盤資料')
    inputs,digest,encoded=load_inputs(inputs_path)
    if inputs.get('as_of')!=asof:raise ValueError('V11 指定日與 Alpha 原始資料不符，請先完成資料更新')
    ctx=context(inputs,stamp);target=_store(store)
    target.mkdir(parents=True,exist_ok=True)
    with (target/'.publish.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);rows=_rows(target);current=rows[0]['model'] if rows else None
        if revision!=(current['revision'] if current else 0):raise ValueError('V11 版號已變更，請重新讀取')
        if current and (asof<current['as_of'] or stamp.isoformat()<current['recorded_at']):raise ValueError('V11 不可倒退資料日或保存時間')
        state=reconcile(current,inputs) if current else _empty()
        if not current:state.update(start=stamp.date().isoformat(),created_at=stamp.isoformat(timespec='seconds'))
        by=_bars(inputs);calendar=sorted(d for d in inputs['dates'] if d<=asof);_mark(state,asof,by,calendar)
        # Every new review supersedes unfilled plans; their original snapshots remain immutable.
        for order in state['pending_orders']:state['order_events'].append(dict(order,status='superseded',date=asof,recorded_at=stamp.isoformat()))
        state['pending_orders']=[];slots=3-len(state['holdings']);held={p['symbol'] for p in state['holdings']}
        earliest=stamp.date() if stamp.time()>=time(9) else stamp.date()-timedelta(days=1)
        not_before=max(asof,earliest.isoformat())
        for c in ctx['candidates']:
            c['status']='已持有' if c['symbol'] in held else '大盤暫停買進' if ctx['blocked'] else '滿倉觀察' if slots<=0 else '已保存買入計畫'
            budget=min(state['cash'],state['equity']/3);c['planned_shares']=min(int(budget/(c['close']*(1+v2.BUY))),int(c['prior_volume']*.01))
            if c['symbol'] in held or ctx['blocked'] or slots<=0:continue
            state['pending_orders'].append(dict(side='buy',symbol=c['symbol'],name=c['name'],signal_date=asof,
                recorded_at=stamp.isoformat(timespec='seconds'),not_before_date=not_before,budget=budget,atr=c['atr'],
                max_qty=int(c['prior_volume']*.01),signal_close=c['close'],reason='正 α、HAC t 排名與趨勢條件通過'))
            slots-=1
        previous=state['days'].get(asof,{})
        state['days'][asof]=dict(candidates=copy.deepcopy(ctx['candidates']),holdings=copy.deepcopy(state['holdings']),
            buy=previous.get('buy',[]),sell=previous.get('sell',[]),cash=state['cash'],equity=state['equity'],kind='手動決策',blocked=ctx['blocked'])
        if not any(p['date']==asof for p in state['activity']):state['activity'].append(dict(date=asof,buy=[],sell=[],hold=[p['symbol'] for p in state['holdings']]))
        state.update(available=True,revision=revision+1,recorded_at=stamp.isoformat(timespec='seconds'),input_sha256=digest,
            review=dict(analysis=analysis.strip(),sources=copy.deepcopy(sources),context=ctx),
            research_note='α 是過去 120 日回歸截距，t 值不是下次上漲機率。研究表現對起跑日敏感，未證明穩定優於 V10；本帳戶不併入歷史回測獲利。')
        row=dict(version=VERSION,revision=revision+1,as_of=asof,recorded_at=state['recorded_at'],model=state)
        content=json.dumps(row,ensure_ascii=False,allow_nan=False)
        archive=target/'inputs'/(digest+'.json.gz');archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            with gzip.open(archive,'xb') as f:f.write(encoded)
        fd,tmp=tempfile.mkstemp(prefix='.snapshot-',suffix='.tmp',dir=target)
        try:
            with os.fdopen(fd,'w') as f:f.write(content);f.flush();os.fsync(f.fileno())
            os.replace(tmp,target/f"{revision+1:06d}-{stamp.strftime('%Y%m%dT%H%M%S')}.json")
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return report(target)
def main():
    p=argparse.ArgumentParser(description='V11 隨 V3、V6、V10 依對話要求手動更新')
    p.add_argument('command',choices=['report','publish']);p.add_argument('--decision',type=Path);p.add_argument('--inputs',type=Path);p.add_argument('--store',type=Path);args=p.parse_args()
    if args.command=='publish':
        if not args.decision:p.error('publish 需提供 --decision')
        result=publish(json.loads(args.decision.read_text()),inputs_path=args.inputs,store=args.store)
    else:result=report(args.store)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
