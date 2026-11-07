"""V3 manual paper account. GET/report never values, trades, initializes, or writes.

Only the explicit CLI review command consumes a dated decision. No scheduler or
update-all registration exists. Previously committed stops/orders are reconciled
on the next manual review; new judgements never rewrite prior decisions.
"""
from __future__ import annotations
import argparse
import copy
from datetime import date, datetime, time
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from zoneinfo import ZoneInfo
from . import db, v2

VERSION = 'v3-manual-two-leaders-20261015'
INITIAL = 100_000.
TZ = ZoneInfo('Asia/Taipei')
CONFIG = dict(slots=2, cap=.5, risk=.04, min_score=80., protect=.08, gap=.04)
VALIDATION_PATH = Path(__file__).with_name('v3_validation.json')


def timestamp():
    return datetime.now(TZ).isoformat(timespec='seconds')


def account_path(path=None):
    return Path(path) if path is not None else db.DATA / 'v3-account.json'


def _number(value, name, minimum=0, maximum=math.inf):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{name}不是有效數值')
    return float(value)


def _date(value):
    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:
        raise ValueError('日期格式必須為 YYYY-MM-DD')
    return value


def _empty():
    return dict(version=VERSION, revision=0, initial=INITIAL, created_at=None,
                start=None, as_of=None, cash=INITIAL, equity=INITIAL, holdings=[],
                pending_orders=[], trades=[], journal=[], curve=[], review=None,
                realized_pnl=0., unrealized_pnl=0., portfolio_return_pct=0.)


def _read(path):
    if not path.exists():
        return _empty()
    value=json.loads(path.read_text())
    if value.get('version')!=VERSION or value.get('initial')!=INITIAL:
        raise ValueError('V3 帳戶版本不符，保留原檔並停止操作')
    return value


def report(path=None):
    """Read saved state only, including when called by daily-follow GET."""
    state=_read(account_path(path))
    validation=json.loads(VALIDATION_PATH.read_text()) if VALIDATION_PATH.exists() else None
    from . import v3_market_study
    saved_study=v3_market_study.read_study(v3_market_study.study_path(path))
    # A manual review saves its study in the same atomic account write. Once that
    # happens an older independent study must not be presented as the new review.
    sync=state.get('market_study_sync')
    market_study=sync.get('study') if sync else saved_study
    if saved_study and sync and saved_study['recorded_at']>sync['recorded_at']:
        market_study=saved_study
    completed=state['trades'];wins=sum(t['pnl']>0 for t in completed)
    return dict(state,key='v3',label='V3 手動研究帳戶',available=bool(state['created_at']),
                mode='manual',description='10 萬元獨立模擬帳戶；只在對話要求更新 V3 持股後保存決策。',
                open_positions=state['holdings'],hit_rate=wins/len(completed)*100 if completed else None,
                wins=wins,entries=len(completed)+len(state['holdings']),
                validation=validation,market_study=market_study,
                market_study_status=sync.get('status') if sync else None,
                update_policy='全部更新、更新今日名單、切換日期及重整頁面只讀取 V3 已保存紀錄；不重算、不選股、不更改持股或評價。',
                execution_note='這是模擬帳戶，不會代送券商委託。指令日收盤決策、最早下一交易日開盤試算；已承諾的停損／買賣計畫，在下次手動更新時按後續行情核對。停損線在兩次更新之間固定，不逐日自行提高。')


def _atomic(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,ensure_ascii=False,allow_nan=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def initialize(context, memo, path=None):
    path=account_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists():raise ValueError('V3 已建立，不覆寫本金或歷史')
        state=_empty();day=_date(context['as_of'])
        state.update(created_at=timestamp(),start=day,as_of=day,
                     review=dict(as_of=day,market=context,analysis=memo,actions=[],target_exposure_pct=0,
                                 conclusion='帳戶已建立；持有現金 100,000 元，等待你要求更新 V3 持股。'))
        _mark(state,day,{})
        state['journal'].append(dict(revision=0,recorded_at=state['created_at'],as_of=day,
                                     kind='initialize',review=copy.deepcopy(state['review']),holdings=[],cash=INITIAL,equity=INITIAL))
        _atomic(path,state)
    return state


def market_context(source=None):
    """Only called by explicit research/review, never by report/GET."""
    source=Path(source or db.DATA/'monitor.sqlite3')
    model=v2.report(source)
    if not model.get('available'):raise ValueError(model.get('error') or '市場資料不足')
    day=model['as_of']
    with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rs_day=con.execute('SELECT MAX(date) FROM rs_days').fetchone()[0]
        branch_day=con.execute("SELECT MAX(s.date) FROM snapshots s JOIN branches b USING(broker,branch) WHERE b.active=1 AND s.status='observed'").fetchone()[0]
        rows=[dict(r) for r in con.execute('SELECT date,symbol,close,high,low,volume FROM prices WHERE date<=? ORDER BY date,symbol',(day,))]
    import pandas as pd
    import numpy as np
    raw=pd.DataFrame(rows);c=raw.pivot(index='date',columns='symbol',values='close').sort_index()
    ma=c.rolling(20,min_periods=20).mean();plain=[s for s in c.columns if len(s)==4 and s.isdigit() and not s.startswith('0')]
    valid=ma.loc[day,plain].notna() & c.loc[day,plain].notna()
    breadth=float((c.loc[day,plain][valid]>ma.loc[day,plain][valid]).mean()*100) if valid.any() else None
    index={}
    for symbol in ['TAIEX','TPEX']:
        cp=c.at[day,symbol];avg=ma.at[day,symbol]
        index[symbol]=dict(close=float(cp) if np.isfinite(cp) else None,sma20=float(avg) if np.isfinite(avg) else None,
                           above=bool(np.isfinite(cp) and np.isfinite(avg) and cp>=avg))
    ready=bool(rs_day and branch_day and rs_day>=day and branch_day>=day and all(v['close'] is not None and v['sma20'] is not None for v in index.values()))
    blocked=not ready or not any(v['above'] for v in index.values())
    candidates=[]
    for r in model['days'][day]['candidates']:
        if r['score']<CONFIG['min_score']:continue
        r=copy.deepcopy(r);r['status']='大盤暫停買進' if blocked else '待手動研究'
        candidates.append(r)
    # ATR for open positions may be needed even when a stock leaves the candidates.
    hi=raw.pivot(index='date',columns='symbol',values='high').reindex_like(c)
    lo=raw.pivot(index='date',columns='symbol',values='low').reindex_like(c)
    atr=np.maximum(hi-lo,np.maximum(abs(hi-c.shift()),abs(lo-c.shift()))).rolling(14,min_periods=14).mean()
    stats={s:dict(close=float(c.at[day,s]),atr=float(atr.at[day,s])) for s in c.columns
           if np.isfinite(c.at[day,s]) and np.isfinite(atr.at[day,s])}
    return dict(as_of=day,rs_date=rs_day,branch_date=branch_day,ready=ready,blocked=blocked,
                indices=index,breadth_pct=breadth,candidates=candidates,stock_stats=stats,
                note='廣度為有20日均線之四碼普通股站上20MA比例；僅作市場參考，不假設它能預測勝率。分點只涵蓋本機追蹤樣本。')


def _fillable(bar):
    return bool(bar and all(isinstance(bar.get(k),(int,float)) and math.isfinite(bar[k]) for k in ['open','high','low','volume'])
                and bar['open']>0 and bar['high']>bar['low'] and bar['volume']>0)


def _close(state, pos, day, price, reason):
    proceeds=pos['qty']*price*(1-v2.SELL)
    state['cash']+=proceeds
    state['trades'].append(dict(pos,exit_date=day,exit_price=price,proceeds=proceeds,
                               pnl=proceeds-pos['cost'],return_pct=(proceeds/pos['cost']-1)*100,exit_reason=reason))
    state['holdings'].remove(pos)


def _mark(state,day,bars):
    unrealized=0.
    for pos in state['holdings']:
        bar=bars.get(pos['symbol'],{});cp=bar.get('close')
        if isinstance(cp,(int,float)) and math.isfinite(cp) and cp>0:
            pos['mark_price']=cp;pos['mark_date']=day;pos['peak']=max(pos['peak'],cp)
        value=pos['qty']*pos['mark_price']*(1-v2.SELL)
        pos.update(value=value,pnl=value-pos['cost'],return_pct=(value/pos['cost']-1)*100)
        unrealized+=pos['pnl']
    realized=sum(t['pnl'] for t in state['trades'])
    equity=state['cash']+sum(p['value'] for p in state['holdings'])
    if state['cash']<-.00001 or abs(equity-(INITIAL+realized+unrealized))>.001:raise ValueError('V3 帳戶金額不平衡，停止寫入')
    state.update(as_of=day,equity=equity,realized_pnl=realized,unrealized_pnl=unrealized,portfolio_return_pct=(equity/INITIAL-1)*100)
    point=dict(date=day,equity=equity,cash=state['cash'],value=state['portfolio_return_pct'],open_positions=len(state['holdings']),closed_positions=len(state['trades']))
    if state['curve'] and state['curve'][-1]['date']==day:state['curve'][-1]=point
    else:state['curve'].append(point)


def reconcile(state, calendar, bars_by_day):
    """Replay ONLY already committed orders and fixed stops on a manual request."""
    state=copy.deepcopy(state)
    for day in calendar:
        if day<=state['as_of']:continue
        bars=bars_by_day.get(day,{})
        sell_orders=[o for o in state['pending_orders'] if o['side']=='sell' and day>o['not_before_date']]
        exited=set()
        for pos in list(state['holdings']):
            b=bars.get(pos['symbol'],{});planned=next((o for o in sell_orders if o['symbol']==pos['symbol']),None)
            gap=bool(b.get('open') and b['open']<=pos['stop'])
            if planned or pos.get('pending_exit') or gap:
                if _fillable(b):
                    _close(state,pos,day,b['open'],planned['reason'] if planned else '既定停損／跳空退出');exited.add(pos['symbol'])
                    if planned:state['pending_orders'].remove(planned)
                else:pos['pending_exit']=True
        for order in list(state['pending_orders']):
            if order['side']!='buy' or day<=order['not_before_date']:continue
            state['pending_orders'].remove(order)
            b=bars.get(order['symbol'],{})
            if len(state['holdings'])>=CONFIG['slots'] or order['symbol'] in exited or any(p['symbol']==order['symbol'] for p in state['holdings']) or not _fillable(b):
                state['order_events'].append(dict(order,status='cancelled',date=day,message='空位／可成交行情不足，取消單次計畫'));continue
            op=b['open']
            if op>order['signal_close']*(1+CONFIG['gap']):
                state['order_events'].append(dict(order,status='cancelled',date=day,message='開高超過訊號收盤4%，不追價'));continue
            stop=v2.tick_floor(max(op-3*order['atr'],op*.92))
            equity=state['cash']+sum(p['qty']*p['mark_price']*(1-v2.SELL) for p in state['holdings'])
            used=equity-state['cash']
            budget=min(state['cash'],order['budget'],equity*CONFIG['cap'],max(0,equity*order['exposure_pct']/100-used),equity*CONFIG['risk']/(1-stop/op+v2.BUY+v2.SELL))
            qty=min(int(budget/(op*(1+v2.BUY))),order['max_qty'])
            if qty<=0:
                state['order_events'].append(dict(order,status='cancelled',date=day,message='可用現金或風險預算不足'));continue
            cost=qty*op*(1+v2.BUY);state['cash']-=cost
            state['holdings'].append(dict(symbol=order['symbol'],name=order['name'],qty=qty,cost=cost,entry_date=day,entry_price=op,signal_date=order['signal_date'],mark_price=op,mark_date=day,peak=op,stop=stop,thesis=order['thesis'],invalidation=order['invalidation']))
            state['order_events'].append(dict(order,status='filled',date=day,qty=qty,price=op,cost=cost))
        for pos in list(state['holdings']):
            b=bars.get(pos['symbol'],{})
            if b.get('low') is not None and b['low']<=pos['stop']:
                if _fillable(b):_close(state,pos,day,min(b['open'],pos['stop']),'先前已承諾的盤中防線')
                else:pos['pending_exit']=True
        _mark(state,day,bars)
    return state


def _load_bars(source,after,through):
    with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute('SELECT date,symbol,open,high,low,close,volume FROM prices WHERE date>? AND date<=? ORDER BY date,symbol',(after,through))]
    calendar=sorted({r['date'] for r in rows if r['symbol']=='TAIEX' and r['close'] is not None})
    bars={}
    for r in rows:bars.setdefault(r['date'],{})[r['symbol']]=r
    return calendar,bars


def apply_review(decision, source=None, path=None, context=None, now=None):
    """Explicit CLI only. Optimistic revision guard plus OS lock; atomic commit."""
    path=account_path(path);source=Path(source or db.DATA/'monitor.sqlite3')
    now=(now or datetime.now(TZ)).astimezone(TZ)
    if not path.exists():raise ValueError('請先建立 V3 帳戶')
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=_read(path)
        if decision.get('expected_revision')!=old['revision']:raise ValueError('V3 版本已改變，請重新讀取後再決策')
        ctx=context if context is not None else market_context(source)
        day=_date(decision.get('as_of'))
        if day!=ctx['as_of'] or day<old['as_of'] or day>now.date().isoformat():raise ValueError('只接受最新可用收盤日，不允許回填歷史決策')
        if not isinstance(decision.get('analysis'),str) or len(decision['analysis'].strip())<10:raise ValueError('請保存市場判斷與資料依據')
        exposure=_number(decision.get('target_exposure_pct'),'股票目標比例',0,100)
        if ctx['blocked'] and exposure>0:raise ValueError('市場／資料濾網未通過，新買入預算必須為零；既有部位可依原防線持有')
        calendar,bars=_load_bars(source,old['as_of'],day)
        state=copy.deepcopy(old);state.setdefault('order_events',[])
        state=reconcile(state,calendar,bars)
        if state['as_of']!=day:raise ValueError('本次評價日行情不完整')
        # Unexecuted prior buy plans must be explicitly replaced by this review.
        for order in state['pending_orders']:
            state['order_events'].append(dict(order,status='replaced',date=day,message='新手動決策取代未執行計畫'))
        state['pending_orders']=[]
        candidates={r['symbol']:r for r in ctx['candidates']}
        seen=set();plans=[];buy_budget=0.
        for action in decision.get('actions',[]):
            side=action.get('side');symbol=action.get('symbol')
            if side not in {'buy','sell'} or symbol in seen:raise ValueError('每檔每次只能有一項買賣決策')
            seen.add(symbol)
            if not isinstance(action.get('reason'),str) or not action['reason'].strip():raise ValueError('買賣決策需附理由')
            base=dict(side=side,symbol=symbol,signal_date=day,not_before_date=max(day,now.date().isoformat()),reason=action['reason'])
            held=next((p for p in state['holdings'] if p['symbol']==symbol),None)
            if side=='sell':
                if not held:raise ValueError('無持股不能賣出')
                plans.append(dict(base,name=held['name']));continue
            if held or ctx['blocked'] or symbol not in candidates:raise ValueError('股票已持有或未通過候選／市場條件')
            r=candidates[symbol]
            if not action.get('thesis') or not action.get('invalidation'):raise ValueError('買進需附投資論點與失效條件')
            budget=_number(action.get('budget'),'買進預算',1,state['equity']*CONFIG['cap'])
            buy_budget+=budget
            with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as con:
                row=con.execute('SELECT volume FROM prices WHERE date=? AND symbol=?',(day,symbol)).fetchone()
            volume=row[0] if row else 0
            plans.append(dict(base,name=r['name'],budget=budget,exposure_pct=exposure,signal_close=r['close'],atr=r['atr'],max_qty=int((volume or 0)*.01),thesis=action['thesis'],invalidation=action['invalidation']))
        sells={p['symbol'] for p in plans if p['side']=='sell'}
        retained=[p for p in state['holdings'] if p['symbol'] not in sells]
        buy_count=sum(p['side']=='buy' for p in plans)
        if len(retained)+buy_count>CONFIG['slots']:raise ValueError('V3 最多兩檔，不允許超額候補買單')
        # Never count unexecuted sell proceeds as cash for a new order.
        room=max(0,state['equity']*exposure/100-sum(p['value'] for p in retained))
        if buy_budget>min(state['cash'],room)+.001:raise ValueError('新買單總預算超過現金或目標部位')
        for pos in state['holdings']:
            stats=ctx.get('stock_stats',{}).get(pos['symbol'])
            if stats:
                pos['peak']=max(pos['peak'],stats['close'])
                mult=2 if pos['peak']>=pos['entry_price']*(1+CONFIG['protect']) else 3
                pos['stop']=max(pos['stop'],v2.tick_floor(pos['peak']-mult*stats['atr']))
        state['pending_orders']=plans
        public_ctx={k:v for k,v in ctx.items() if k!='stock_stats'}
        state['review']=dict(as_of=day,market=public_ctx,analysis=decision['analysis'],target_exposure_pct=exposure,actions=plans,sources=decision.get('sources',[]),conclusion=decision.get('conclusion','已保存手動決策'))
        # Only an explicit V3 review can refresh research. After 07:00 local time
        # the saved study is built from the same dated decision and market context.
        # It remains a previous-close study until separately sourced pre-open data
        # are supplied; no overseas quote is silently inferred.
        if now.time()>=time(7):
            from . import v3_market_study
            prior=state.get('market_study_sync',{}).get('study') or v3_market_study.read_study(v3_market_study.study_path(path))
            supplied=decision.get('market_study')
            if supplied is not None and not isinstance(supplied,dict):raise ValueError('V3 市場研究必須是物件')
            supplied=supplied or {}
            auto=dict(expected_revision=prior['revision'] if prior else 0,as_of=day,
                      analysis=decision['analysis'].strip()+'；本次位階僅依已確認的收盤資料，不把未取得的盤前行情當成事實。',
                      buy_condition='同日行情、RS與分點資料完整，市場濾網通過，候選達80分且完成人工審核後才考慮買進。',
                      stock_target_pct=exposure,bond_target_pct=0,
                      bond_reason='本次未核對債券商品行情、利率與匯率，暫以現金保留未投入資金。',
                      sources=decision.get('sources',[]))
            auto.update(supplied)
            # A caller may add richer research but may not separate its date or
            # version from the account review that stores it.
            auto['as_of']=day;auto['expected_revision']=prior['revision'] if prior else 0
            try:
                study=v3_market_study.build_snapshot(auto,source,ctx,prior,now)
                status='已隨本次 V3 手動更新同步保存；研究依據為收盤資料。'
            except ValueError as exc:
                if supplied:raise
                study=None
                status=f'本次 V3 已更新，但市場研究缺少可驗證的指數資料：{exc}'
            state['market_study_sync']=dict(recorded_at=now.isoformat(timespec='seconds'),as_of=day,
                                            study=study,status=status)
        state['revision']+=1
        state['journal'].append(dict(revision=state['revision'],recorded_at=now.isoformat(timespec='seconds'),as_of=day,kind='manual_review',review=copy.deepcopy(state['review']),holdings=copy.deepcopy(state['holdings']),cash=state['cash'],equity=state['equity']))
        _atomic(path,state)
    return state


def main():
    parser=argparse.ArgumentParser(description='V3 只能依使用者明確要求手動更新')
    parser.add_argument('command',choices=['context','init','review','report'])
    parser.add_argument('--source');parser.add_argument('--account');parser.add_argument('--decision')
    args=parser.parse_args()
    if args.command=='context':
        ctx=market_context(args.source);ctx.pop('stock_stats',None);print(json.dumps(ctx,ensure_ascii=False,allow_nan=False,indent=2));return
    if args.command=='report':print(json.dumps(report(args.account),ensure_ascii=False,allow_nan=False));return
    if not args.decision:parser.error('init/review 必須提供 --decision 決策檔')
    decision=json.loads(Path(args.decision).read_text())
    if args.command=='init':
        ctx=market_context(args.source);ctx.pop('stock_stats',None)
        result=initialize(ctx,decision['analysis'],args.account)
    else:result=apply_review(decision,args.source,args.account)
    print(json.dumps(dict(revision=result['revision'],as_of=result['as_of'],cash=result['cash'],holdings=len(result['holdings']),pending_orders=len(result['pending_orders'])),ensure_ascii=False))

if __name__=='__main__':main()
