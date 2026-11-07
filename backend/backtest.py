"""Frozen report observations and forward-only paper evaluation. No real orders."""
from __future__ import annotations
import json, math, re, threading, calendar
import requests
from datetime import date, datetime, timedelta, time
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from . import research, sources, rs, sector_flow

_lock=threading.Lock()
VERSION='candidate-recommendation-v4-alpha'
METHOD='新報告從券商、RS與族群候選中提出個股建議，研究日DMI須符合+DI14>-DI14；SAR低於收盤只作加分。下一交易日開盤建立紙上部位，依凍結的停利停損出場。每次候選特徵、模型版本與結果永久保存；樣本達門檻後，以只使用當時已知結果的時間序列嶺迴歸估計超額報酬。績效扣估計手續費折讓、交易稅與雙邊滑價；alpha為成本後報酬減同期加權指數報酬。舊報告沿用當時規則。'

def init_futures(c):
    """Create only the tables used by the active futures journal."""
    schema='''
    CREATE TABLE IF NOT EXISTS bt_predictions(report_id TEXT PRIMARY KEY,basis_date TEXT,created_at TEXT,target_date TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_future_results(report_id TEXT PRIMARY KEY,payload TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS bt_model_runs(report_id TEXT PRIMARY KEY,basis_date TEXT,created_at TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_futures(date TEXT,contract TEXT,payload TEXT,PRIMARY KEY(date,contract));
    CREATE TABLE IF NOT EXISTS bt_calendars(year INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_sessions(date TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS bt_meta(key TEXT PRIMARY KEY,payload TEXT);
    '''
    for statement in schema.split(';'):
        if statement.strip():c.execute(statement)

def init(c):
    schema='''
    CREATE TABLE IF NOT EXISTS bt_observations(id TEXT PRIMARY KEY,report_id TEXT,symbol TEXT,name TEXT,basis_date TEXT,created_at TEXT,entry_date TEXT,take_profit REAL,stop_loss REAL,reason TEXT,rule_set_at TEXT);
    CREATE TABLE IF NOT EXISTS bt_observation_archive(id TEXT PRIMARY KEY,payload TEXT,result TEXT,archived_at TEXT);
    CREATE TABLE IF NOT EXISTS bt_predictions(report_id TEXT PRIMARY KEY,basis_date TEXT,created_at TEXT,target_date TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_box_rules(id TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_recommendation_rules(id TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_results(id TEXT PRIMARY KEY,payload TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS bt_future_results(report_id TEXT PRIMARY KEY,payload TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS bt_model_runs(report_id TEXT PRIMARY KEY,basis_date TEXT,created_at TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_model_features(id TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_prices(date TEXT,symbol TEXT,payload TEXT,PRIMARY KEY(date,symbol));
    CREATE TABLE IF NOT EXISTS bt_futures(date TEXT,contract TEXT,payload TEXT,PRIMARY KEY(date,contract));
    CREATE TABLE IF NOT EXISTS bt_calendars(year INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bt_sessions(date TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS bt_actual_positions(symbol TEXT PRIMARY KEY,observation_id TEXT NOT NULL,name TEXT NOT NULL,buy_date TEXT NOT NULL,buy_price REAL NOT NULL,shares INTEGER NOT NULL,take_profit REAL,stop_loss REAL,box_bottom REAL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bt_meta(key TEXT PRIMARY KEY,payload TEXT);
    '''
    for statement in schema.split(';'):
        if statement.strip():c.execute(statement)
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='bt_one_symbol_per_day'").fetchone():
        duplicates=c.execute('SELECT basis_date,symbol FROM bt_observations GROUP BY basis_date,symbol HAVING count(*)>1').fetchall()
        for day,symbol in duplicates:
            rows=[dict(r) for r in c.execute('SELECT * FROM bt_observations WHERE basis_date=? AND symbol=?',(day,symbol))]
            # Prefer the earliest usable frozen rules; never prefer a profitable outcome.
            keeper=min(rows,key=lambda r:(not has_rules(r),r['rule_set_at'] or r['created_at'],r['created_at'],r['id']))
            for row in rows:
                if row['id']!=keeper['id']:archive_observation(c,row)
        c.execute('CREATE UNIQUE INDEX bt_one_symbol_per_day ON bt_observations(basis_date,symbol)')
    # Recover training features from each report's immutable context without
    # changing its original selection, rules, timestamps, or outcome.
    from . import alpha_model
    for row in c.execute('''SELECT o.id,o.symbol,r.context FROM bt_observations o
                            JOIN reports r ON r.id=o.report_id
                            LEFT JOIN bt_model_features f ON f.id=o.id
                            WHERE f.id IS NULL AND r.context IS NOT NULL''').fetchall():
        try:
            candidate=next(x for x in json.loads(row['context']).get('candidates',[]) if x.get('symbol')==row['symbol'])
            payload={'version':alpha_model.VERSION,'source':'frozen_report_context','features':alpha_model.candidate_features(candidate)}
            c.execute('INSERT OR IGNORE INTO bt_model_features VALUES (?,?)',(row['id'],json.dumps(payload,ensure_ascii=False)))
        except (ValueError,TypeError,StopIteration,json.JSONDecodeError):pass


def has_rules(row):
    return number(row.get('take_profit')) and number(row.get('stop_loss')) and row['stop_loss']<row['take_profit'] and bool(row.get('rule_set_at'))


def archive_observation(c,row):
    result=c.execute('SELECT payload FROM bt_results WHERE id=?',(row['id'],)).fetchone()
    c.execute('INSERT OR IGNORE INTO bt_observation_archive VALUES (?,?,?,?)',(row['id'],json.dumps(row,ensure_ascii=False),result[0] if result else None,research.now()))
    c.execute('DELETE FROM bt_results WHERE id=?',(row['id'],))
    c.execute('DELETE FROM bt_box_rules WHERE id=?',(row['id'],))
    c.execute('DELETE FROM bt_recommendation_rules WHERE id=?',(row['id'],))
    c.execute('DELETE FROM bt_model_features WHERE id=?',(row['id'],))
    c.execute('DELETE FROM bt_observations WHERE id=?',(row['id'],))


def number(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0

def parse_calendar(data,year):
    if data.get('stat')!='ok' or str(data.get('date',''))[:4]!=str(year) or data.get('fields')!=['日期','名稱','說明']:raise ValueError('交易日曆格式或年份不符')
    closed=set();opened=set()
    for day,name,_ in data.get('data',[]):
        date.fromisoformat(day)
        if not day.startswith(str(year)):raise ValueError('日曆含其他年份')
        (opened if ('開始交易' in name or '最後交易日' in name) else closed).add(day)
    if not closed:raise ValueError('休市資料缺少')
    return {'closed':sorted(closed),'opened':sorted(opened),'source':'https://www.twse.com.tw/zh/trading/holiday.html','saved_at':research.now()}

def calendar_year(year):
    with research.store() as c:
        init_futures(c);r=c.execute('SELECT payload FROM bt_calendars WHERE year=?',(year,)).fetchone()
    if r:return json.loads(r[0])
    data=parse_calendar(json.loads(sources.get('https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?'+urlencode({'response':'json','queryYear':year}),refresh=True)),year)
    with research.store() as c:c.execute('INSERT OR REPLACE INTO bt_calendars VALUES (?,?)',(year,json.dumps(data)))
    return data

def next_day(basis,fetch=False):
    start=date.fromisoformat(basis)
    for i in range(1,35):
        d=start+timedelta(days=i);day=d.isoformat()
        if fetch:cal=calendar_year(d.year)
        else:
            with research.store() as c:
                init_futures(c);r=c.execute('SELECT payload FROM bt_calendars WHERE year=?',(d.year,)).fetchone()
            if not r:return None
            cal=json.loads(r[0])
        if day in cal['opened'] or d.weekday()<5 and day not in cal['closed']:return day
    return None

def legacy_picks(result,evidence):
    text=''.join(p['text'] for p in result.get('parts',[]))
    match=re.search(r'(?m)^#{1,3}\s*個股觀察建議[^\n]*\n(.*?)(?=^#{1,3}\s|\Z)',text,re.S)
    if not match:return []
    rows=[];seen=set()
    candidates={r['symbol']:r for r in evidence.get('candidates',[])}
    for line in match[1].splitlines():
        m=re.match(r'^\s*(?:[-*]|\d+[.)、])?\s*\**(\d{4})\b',line)
        if m and m[1] in candidates and m[1] not in seen:
            seen.add(m[1]);row=candidates[m[1]]
            rows.append({'symbol':m[1],'name':row.get('name',m[1]),'take_profit':None,'stop_loss':None,'reason':line.strip()})
    return rows

def ingest(c,report_id,basis,created,result,evidence):
    init(c)
    tracking=result.get('tracking',{})
    stock_tracking=(evidence.get('generation_policy') or {}).get('stock_tracking',True)
    picks=(tracking.get('observations') if 'observations' in tracking else legacy_picks(result,evidence)) if stock_tracking else []
    # Calendar preparation takes place before generation or during manual refresh.
    target=evidence.get('execution',{}).get('target_date')
    levels={r['symbol']:r for r in evidence.get('box_strategy',{}).get('levels',[]) if isinstance(r,dict) and r.get('symbol')}
    candidates={r['symbol']:r for r in evidence.get('candidates',[]) if isinstance(r,dict) and r.get('symbol')}
    alpha_scores={r['symbol']:r for r in evidence.get('alpha_model',{}).get('stock',{}).get('candidates',[]) if isinstance(r,dict) and r.get('symbol')}
    for p in picks:
        existing=c.execute('SELECT * FROM bt_observations WHERE basis_date=? AND symbol=?',(basis,p['symbol'])).fetchone()
        entry_date=target
        if existing:
            old=dict(existing)
            incoming={**p,'rule_set_at':created if p.get('take_profit') is not None else None}
            if has_rules(old) or not has_rules(incoming):continue
            # A later report may supply previously missing rules, but cannot change frozen ones.
            entry_date=target or old['entry_date']
            archive_observation(c,old)
        ident=report_id+':'+p['symbol']
        c.execute('INSERT OR IGNORE INTO bt_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)',(ident,report_id,p['symbol'],p.get('name',p['symbol']),basis,created,entry_date,p.get('take_profit'),p.get('stop_loss'),p.get('reason',''),created if p.get('take_profit') is not None else None))
        if c.execute('SELECT 1 FROM bt_observations WHERE id=?',(ident,)).fetchone():
            from . import alpha_model
            candidate=candidates.get(p['symbol'],{})
            c.execute('INSERT OR IGNORE INTO bt_model_features VALUES (?,?)',(ident,json.dumps({'version':alpha_model.VERSION,'source':'frozen_report_context','features':alpha_model.candidate_features(candidate)},ensure_ascii=False)))
        if c.execute('SELECT 1 FROM bt_observations WHERE id=?',(ident,)).fetchone() and tracking.get('version')==VERSION:
            level=levels.get(p['symbol'],{})
            from . import alpha_model
            rule={'version':VERSION,'box_bottom':p.get('box_bottom',level.get('box_bottom')),'technical':p.get('technical'),
                  'features':alpha_model.candidate_features(candidate),'alpha_prediction':alpha_scores.get(p['symbol'])}
            c.execute('INSERT OR IGNORE INTO bt_recommendation_rules VALUES (?,?)',(ident,json.dumps(rule,ensure_ascii=False)))
    prediction=tracking.get('futures')
    if prediction is not None:
        c.execute('INSERT OR IGNORE INTO bt_predictions VALUES (?,?,?,?,?)',(report_id,basis,created,target,json.dumps(prediction,ensure_ascii=False)))
    if evidence.get('alpha_model'):
        c.execute('INSERT OR IGNORE INTO bt_model_runs VALUES (?,?,?,?)',(report_id,basis,created,json.dumps(evidence['alpha_model'],ensure_ascii=False)))

def ingest_futures(c,report_id,basis,created,result,evidence):
    init_futures(c)
    tracking=result.get('tracking') or {}
    prediction=tracking.get('futures')
    target=(evidence.get('execution') or {}).get('target_date')
    if prediction is not None:
        c.execute('INSERT OR IGNORE INTO bt_predictions VALUES (?,?,?,?,?)',(report_id,basis,created,target,json.dumps(prediction,ensure_ascii=False)))
    if evidence.get('alpha_model'):
        c.execute('INSERT OR IGNORE INTO bt_model_runs VALUES (?,?,?,?)',(report_id,basis,created,json.dumps(evidence['alpha_model'],ensure_ascii=False)))


def migrate():
    with research.store() as c:
        init(c)
        for r in c.execute("SELECT * FROM reports WHERE status='complete' ORDER BY COALESCE(finished_at,created_at),created_at,id").fetchall():
            ingest(c,r['id'],r['date'],r['finished_at'] or r['created_at'],json.loads(r['result']),json.loads(r['context']))

def initialize():
    migrate()
    with research.store() as c:
        old=c.execute("SELECT payload FROM bt_meta WHERE key='job'").fetchone()
        if old and json.loads(old[0]).get('status')=='running':set_meta(c,'job',{'status':'interrupted','message':'上次更新中斷；已保存資料保留，請手動更新'})

def initialize_futures():
    """Initialize the active journal without migrating archived stock observations."""
    with research.store() as c:
        init_futures(c)
        old=c.execute("SELECT payload FROM bt_meta WHERE key='job'").fetchone()
        if old and json.loads(old[0]).get('status')=='running':set_meta(c,'job',{'status':'interrupted','message':'上次台指更新中斷；已保存預測保留，請手動更新'})

def set_meta(c,key,payload):c.execute('INSERT OR REPLACE INTO bt_meta VALUES (?,?)',(key,json.dumps(payload,ensure_ascii=False)))
def job():
    with research.store() as c:
        init_futures(c);r=c.execute("SELECT payload FROM bt_meta WHERE key='job'").fetchone()
    return json.loads(r[0]) if r else {'status':'idle','message':'尚未更新回測行情'}

def stamp_before(stamp,day,clock):
    try:return datetime.fromisoformat(stamp).astimezone(research.TZ)<datetime.combine(date.fromisoformat(day),clock,research.TZ)
    except (ValueError,TypeError):return False

def evaluate(row,days,quotes,as_of):
    result={'status':'pending','message':'等待交易日曆','entry_date':row.get('entry_date'),'entry_price':None,'exit_date':None,'exit_price':None,'held_days':None,'pnl':None,'return_pct':None,'unrealized_pnl':None,'mark_date':None,'ambiguous':False}
    entry=row.get('entry_date')
    if not entry:return result
    if not number(row.get('take_profit')) or not number(row.get('stop_loss')):
        return dict(result,status='rules_missing',message='原報告沒有數值停利／停損；待補規則，不推定交易')
    if entry>as_of:return dict(result,message='等待下一交易日開盤')
    if entry not in days:return dict(result,status='gap',message='預定交易日尚無官方行情；休市或缺項待確認')
    sessions=[d for d in days if entry<=d<=as_of]
    buy=quotes.get(entry)
    if not valid_bar(buy):return dict(result,status='gap',message='缺少預定買入日開高低收，保留待回補')
    entry_price=buy['open'];result.update(entry_price=entry_price)
    for n,day in enumerate(sessions):
        bar=quotes.get(day)
        if not valid_bar(bar):return dict(result,status='gap',message=f'{day} 行情缺口；未跳過判定停利停損',unrealized_pnl=None)
        sl,tp=row['stop_loss'],row['take_profit'];price=None;why=''
        if bar['open']<=sl:price=bar['open'];why='開盤已達停損'
        elif bar['open']>=tp:price=bar['open'];why='開盤已達停利'
        elif bar['low']<=sl:price=sl;why='停損';result['ambiguous']=bar['high']>=tp
        elif bar['high']>=tp:price=tp;why='停利'
        result.update(held_days=n,mark_date=day)
        if price is not None:
            return dict(result,status='closed',message=why+('；同日雙觸價先停損' if result['ambiguous'] else ''),exit_date=day,exit_price=price,pnl=round((price-entry_price)*1000,2),return_pct=round((price/entry_price-1)*100,4))
        result.update(status='open',message='尚未碰到停利或停損',unrealized_pnl=round((bar['close']-entry_price)*1000,2),return_pct=round((bar['close']/entry_price-1)*100,4))
    return result

def evaluate_recommendation(row,days,quotes,as_of):
    """Evaluate report-defined levels; the calculated box bottom is display-only."""
    base={'status':'pending','message':'等待交易日曆','entry_date':row.get('entry_date'),'entry_price':None,'exit_date':None,'exit_price':None,'held_days':None,'pnl':None,'net_pnl':None,'return_pct':None,'net_return_pct':None,'r_multiple':None,'unrealized_pnl':None,'mark_date':None,'ambiguous':False,'strategy':'研究推薦固定價位'}
    entry=row.get('entry_date')
    if not entry:return base
    if not number(row.get('take_profit')) or not number(row.get('stop_loss')):
        return dict(base,status='rules_missing',message='原報告沒有數值停利／停損；待補規則，不推定交易')
    if entry>as_of:return dict(base,message='等待下一交易日開盤')
    if entry not in days:return dict(base,status='gap',message='預定交易日尚無官方行情；休市或缺項待確認')
    sessions=[d for d in days if entry<=d<=as_of];buy=quotes.get(entry)
    if not valid_bar(buy):return dict(base,status='gap',message='缺少預定買入日開高低收，保留待回補')
    qty=1000.;entry_price=buy['open'];paid=friction(entry_price,True);capital=paid*qty
    risk=max((entry_price-row['stop_loss'])*qty,entry_price*qty*.001)
    result=dict(base,entry_price=entry_price,capital=round(capital,2),initial_risk=round(risk,2),position_shares=qty)
    for n,day in enumerate(sessions):
        bar=quotes.get(day)
        if not valid_bar(bar):return dict(result,status='gap',message=f'{day} 行情缺口；未跳過判定停利停損',unrealized_pnl=None,net_pnl=None)
        sl,tp=row['stop_loss'],row['take_profit'];price=None;why=''
        if bar['open']<=sl:price=bar['open'];why='開盤已達停損'
        elif bar['open']>=tp:price=bar['open'];why='開盤已達停利'
        elif bar['low']<=sl:price=sl;why='停損';result['ambiguous']=bar['high']>=tp
        elif bar['high']>=tp:price=tp;why='停利'
        result.update(held_days=n,mark_date=day)
        if price is not None:
            gross=(price-entry_price)*qty;net=(friction(price,False)-paid)*qty
            return dict(result,status='closed',message=why+('；同日雙觸價先停損' if result['ambiguous'] else ''),exit_date=day,exit_price=price,position_shares=0,pnl=round(gross,2),net_pnl=round(net,2),unrealized_pnl=0,return_pct=round(gross/capital*100,4),net_return_pct=round(net/capital*100,4),r_multiple=round(net/risk,4))
        gross=(bar['close']-entry_price)*qty;net=(friction(bar['close'],False)-paid)*qty
        result.update(status='open',message='尚未碰到停利或停損',position_shares=qty,pnl=0,net_pnl=0,unrealized_pnl=round(net,2),return_pct=round(gross/capital*100,4),net_return_pct=round(net/capital*100,4),r_multiple=round(net/risk,4))
    return result

def friction(price,buy):
    # Spec v1.0: 0.2% slippage each side, 0.1425% commission at 60% discount,
    # and 0.3% securities transaction tax on sale.
    return price*(1+.002+.001425*.6) if buy else price*(1-.002-.001425*.6-.003)

def evaluate_box(row,rule,days,quotes,as_of):
    base={'status':'pending','message':'等待交易日曆','entry_date':row.get('entry_date'),'entry_price':None,'exit_date':None,'exit_price':None,'held_days':None,'pnl':None,'net_pnl':None,'return_pct':None,'net_return_pct':None,'r_multiple':None,'unrealized_pnl':None,'mark_date':None,'ambiguous':False,'strategy':'箱型回檔'}
    entry=row.get('entry_date')
    if not entry or entry>as_of:return dict(base,message='等待下一交易日開盤')
    if entry not in days:return dict(base,status='gap',message='預定交易日尚無官方行情；休市或缺項待確認')
    sessions=[d for d in days if entry<=d<=as_of];first=quotes.get(entry)
    if not valid_bar(first):return dict(base,status='gap',message='缺少預定買入日開高低收，保留待回補')
    qty=float(rule.get('probe_size_pct',60))*10;entry_price=first['open'];lots=[{'qty':qty,'raw':entry_price,'paid':friction(entry_price,True)}]
    initial_stop=float(rule['initial_stop']);risk=max((entry_price-initial_stop)*qty,entry_price*qty*.001);stop=initial_stop;tp=float(rule['tp1']);circuit=float(rule.get('circuit_breaker_pct',8))/100
    realized_gross=0.;realized_net=0.;sold=0.;tp_done=False;add_done=False;pending_stop=False;pending_add=False;highest_close=None
    result=dict(base,entry_price=entry_price,initial_risk=round(risk,2),capital=round(friction(entry_price,True)*qty,2),position_shares=qty)
    def sell(amount,raw):
        nonlocal realized_gross,realized_net,sold
        amount=min(amount,sum(x['qty'] for x in lots)-sold)
        if amount<=0:return
        average=sum(x['qty']*x['raw'] for x in lots)/sum(x['qty'] for x in lots);paid=sum(x['qty']*x['paid'] for x in lots)/sum(x['qty'] for x in lots)
        realized_gross+=(raw-average)*amount;realized_net+=(friction(raw,False)-paid)*amount;sold+=amount
    for n,day in enumerate(sessions):
        bar=quotes.get(day)
        if not valid_bar(bar):return dict(result,status='gap',message=f'{day} 行情缺口；未跳過策略判定',unrealized_pnl=None,net_pnl=None)
        if pending_stop:
            sell(10_000,bar['open']);return _finish_box(result,day,bar['open'],n,realized_gross,realized_net,risk,'前一日收破移動停損，次日開盤出場')
        if pending_add:
            add_qty=float(rule.get('add_size_pct',40))*10;lots.append({'qty':add_qty,'raw':bar['open'],'paid':friction(bar['open'],True)});qty+=add_qty;add_done=True;pending_add=False
            result['capital']=round(result['capital']+lots[-1]['paid']*add_qty,2);result['position_shares']=qty
            stop=max(stop,sum(x['qty']*x['raw'] for x in lots)/qty)
        trigger=entry_price*(1-circuit)
        if bar['open']<=trigger:
            sell(10_000,bar['open']);return _finish_box(result,day,bar['open'],n,realized_gross,realized_net,risk,'急跌熔斷：開盤已越過門檻')
        if bar['low']<=trigger:
            sell(10_000,trigger);return _finish_box(result,day,trigger,n,realized_gross,realized_net,risk,'急跌熔斷門檻觸發')
        if not tp_done and bar['high']>=tp:
            sell((qty-sold)*.5,tp);tp_done=True
        held=qty-sold
        if held<=1e-9:return _finish_box(result,day,tp,n,realized_gross,realized_net,risk,'TP1 完成出場')
        if not add_done and not pending_add and bar['close']>rule['box_top']:
            vol50=sum((quotes.get(d) or {}).get('volume',0) for d in sessions[max(0,n-49):n+1])/min(50,n+1)
            held_breakout=n>=2 and all(quotes[sessions[j]]['close']>=rule['box_top'] for j in range(n-2,n+1))
            confirmations=sum([bar.get('volume',0)>=vol50*1.5 if vol50 else False,bar['close']>=bar['low']+(bar['high']-bar['low'])*2/3,held_breakout])
            if confirmations>=2:pending_add=True
        # Confirmed N=3 swing lows available as of this close; the stop only ratchets upward.
        if n>=6:
            center=sessions[n-3];window=[quotes[d] for d in sessions[n-6:n+1]]
            if all(valid_bar(x) for x in window) and quotes[center]['low']==min(x['low'] for x in window):
                stop=max(stop,quotes[center]['low']-.3*float(rule.get('atr20') or 0))
        if bar['close']<stop:pending_stop=True
        if n>=int(rule.get('time_stop_days',15))-1 and not add_done:
            sell(10_000,bar['close']);return _finish_box(result,day,bar['close'],n,realized_gross,realized_net,risk,'15個交易日未加碼，收盤時間停損')
        average=sum(x['qty']*x['raw'] for x in lots)/qty;paid=sum(x['qty']*x['paid'] for x in lots)/qty
        unreal_gross=(bar['close']-average)*held;unreal_net=(friction(bar['close'],False)-paid)*held
        result.update(status='open',message=('已達TP1，餘部移動停損' if tp_done else '依箱型規則持有'),held_days=n,mark_date=day,position_shares=held,pnl=round(realized_gross,2),net_pnl=round(realized_net,2),unrealized_pnl=round(unreal_net,2),return_pct=round((realized_gross+unreal_gross)/(average*qty)*100,4),net_return_pct=round((realized_net+unreal_net)/(paid*qty)*100,4),r_multiple=round((realized_net+unreal_net)/risk,4),active_stop=round(stop,2),tp1_done=tp_done,add_on=add_done)
    return result

def _finish_box(result,day,price,held, gross,net,risk,message):
    capital=float(result.get('capital') or result['entry_price']*max(float(result.get('position_shares') or 600),1))
    return dict(result,status='closed',message=message,exit_date=day,exit_price=round(price,4),held_days=held,mark_date=day,position_shares=0,pnl=round(gross,2),net_pnl=round(net,2),unrealized_pnl=0,return_pct=round(gross/capital*100,4),net_return_pct=round(net/capital*100,4),r_multiple=round(net/risk,4))

def valid_bar(row):
    return bool(row) and all(number(row.get(k)) for k in ['open','high','low','close']) and row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']

def parse_futures(html,day):
    soup=BeautifulSoup(html,'html.parser');d=soup.select_one('input[name=queryDate]');session=soup.select_one('input[name=marketCode]')
    if not d or d.get('value')!=day.replace('-','/') or not session or session.get('value')!='0':raise ValueError('期貨回傳日期或交易時段不符')
    text=soup.get_text(' ',strip=True)
    if not re.search(re.escape(day.replace('-','/'))+r'\s+08:45\s*~\s*13:(?:45|30)\s+一般交易時段',text):raise ValueError('找不到指定日期的台指日盤行情')
    rows=[]
    for tr in soup.select('table.table_f tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.select('td')]
        if len(cells)<6 or cells[0]!='TX' or not re.fullmatch(r'\d{6}',cells[1]):continue
        r={'date':day,'contract':cells[1],**dict(zip(['open','high','low','close'],[sources.number(x) for x in cells[2:6]])),'source':'https://www.taifex.com.tw/cht/3/futDailyMarketReport?'+urlencode({'queryDate':day.replace('-','/'),'commodity_id':'TX','marketCode':'0'})}
        if valid_bar(r):rows.append(r)
    if not rows:raise ValueError('台指期日盤行情缺少')
    return rows

def parse_futures_api(data):
    if not isinstance(data,list):raise ValueError('期交所 OpenAPI 回應格式不符')
    rows=[]
    for x in data:
        if x.get('Contract')!='TX' or x.get('TradingSession')!='一般':continue
        d=x.get('Date','');contract=x.get('ContractMonth(Week)','')
        if not re.fullmatch(r'\d{8}',d) or not re.fullmatch(r'\d{6}',contract):continue
        day=date(int(d[:4]),int(d[4:6]),int(d[6:])).isoformat()
        r={'date':day,'contract':contract,**{key:sources.number(x.get(field)) for key,field in [('open','Open'),('high','High'),('low','Low'),('close','Last')]},'source':'https://openapi.taifex.com.tw/v1/DailyMarketReportFut'}
        if valid_bar(r):rows.append(r)
    if not rows:raise ValueError('OpenAPI 缺少台指一般交易時段行情')
    return rows

def fetch_futures(day):
    with research.store() as c:
        init_futures(c);saved=[json.loads(r[0]) for r in c.execute('SELECT payload FROM bt_futures WHERE date=?',(day,))]
    if saved:return saved
    # Latest official open data is preferred; historical reports are a separate public source.
    try:
        response=requests.get('https://openapi.taifex.com.tw/v1/DailyMarketReportFut',timeout=(10,25),allow_redirects=False)
        response.raise_for_status();latest=parse_futures_api(response.json())
        with research.store() as c:c.executemany('INSERT OR REPLACE INTO bt_futures VALUES (?,?,?)',[(r['date'],r['contract'],json.dumps(r)) for r in latest])
        rows=[r for r in latest if r['date']==day]
        if rows:return rows
    except (requests.RequestException,ValueError):pass
    url='https://www.taifex.com.tw/cht/3/futDailyMarketReport?'+urlencode({'queryDate':day.replace('-','/'),'commodity_id':'TX','queryType':'2','marketCode':'0'})
    response=requests.get(url,timeout=(10,25),allow_redirects=False)
    response.raise_for_status()
    rows=parse_futures(response.content,day)
    with research.store() as c:c.executemany('INSERT OR REPLACE INTO bt_futures VALUES (?,?,?)',[(day,r['contract'],json.dumps(r)) for r in rows])
    return rows

def prediction_result(row,bar):
    p=json.loads(row['payload']);ready=all(number(p.get(k)) for k in ['resistance1','resistance2','open','support1','support2'])
    if not ready:return {'status':'missing','message':p.get('reason') or '預測點位缺少'}
    if not bar:return {'status':'pending','message':'等待目標日同契約日盤行情'}
    error=p['open']-bar['open']
    close_error=p.get('close')-bar['close'] if number(p.get('close')) else None
    predicted_direction=(p.get('close')>p['open'])-(p.get('close')<p['open']) if number(p.get('close')) else None
    actual_direction=(bar['close']>bar['open'])-(bar['close']<bar['open'])
    return {'status':'scored','message':'已核對日盤開高低收','actual':bar,'open_error':round(error,2),'absolute_error':round(abs(error),2),'open_error_pct':round(error/bar['open']*100,4),
            'close_error':round(close_error,2) if close_error is not None else None,'close_absolute_error':round(abs(close_error),2) if close_error is not None else None,
            'close_error_pct':round(close_error/bar['close']*100,4) if close_error is not None else None,
            'direction_correct':predicted_direction==actual_direction if predicted_direction is not None else None,
            'inside_outer':bar['low']>=p['support2'] and bar['high']<=p['resistance2'],'touches':{k:bar['low']<=p[k]<=bar['high'] for k in ['resistance1','resistance2','support1','support2']},'break_resistance2':bar['high']>p['resistance2'],'break_support2':bar['low']<p['support2']}

def saved_forecasts():
    """Read the futures journal without loading archived stock observations."""
    with research.store() as c:
        init_futures(c)
        forecasts=[]
        for r in c.execute('SELECT p.*,r.payload AS outcome FROM bt_predictions p LEFT JOIN bt_future_results r USING(report_id) ORDER BY p.basis_date DESC,p.created_at'):
            row=dict(r);row['prediction']=json.loads(row.pop('payload'));row['result']=json.loads(row.pop('outcome') or '{}');forecasts.append(row)
    first={}
    for row in forecasts:
        key=(row['created_at'],row['report_id']);first[row['basis_date']]=min(first.get(row['basis_date'],key),key)
    for row in forecasts:
        row['timely']=bool(row.get('target_date')) and stamp_before(row['created_at'],row['target_date'],time(8,45))
        row['primary']=(row['created_at'],row['report_id'])==first[row['basis_date']]
        row['eligible']=row['timely'] and row['primary']
    return forecasts

def saved_rows():
    with research.store() as c:
        init(c)
        rows=[]
        for r in c.execute('SELECT o.*,r.payload,b.payload AS box_payload,n.payload AS recommendation_payload,m.payload AS model_feature_payload FROM bt_observations o LEFT JOIN bt_results r ON r.id=o.id LEFT JOIN bt_box_rules b ON b.id=o.id LEFT JOIN bt_recommendation_rules n ON n.id=o.id LEFT JOIN bt_model_features m ON m.id=o.id ORDER BY o.basis_date DESC,o.created_at,o.symbol'):
            row=dict(r);row['result']=json.loads(row.pop('payload') or '{}');box_payload=row.pop('box_payload');recommendation_payload=row.pop('recommendation_payload');feature_payload=row.pop('model_feature_payload');row['box_rule']=json.loads(box_payload) if box_payload else None;row['recommendation_rule']=json.loads(recommendation_payload) if recommendation_payload else None;row['model_features']=(json.loads(feature_payload).get('features') if feature_payload else None);rule=row['recommendation_rule'] or row['box_rule'] or {};row['box_bottom']=rule.get('box_bottom');row['technical']=rule.get('technical');rows.append(row)
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_index_bars'").fetchone():
            index_closes={r[0]:r[1] for r in c.execute('SELECT date,close FROM risk_index_bars WHERE close>0')}
        else:index_closes={}
        for row in rows:
            result=row['result'];start=index_closes.get(result.get('entry_date'));end=index_closes.get(result.get('exit_date'));net_return=result.get('net_return_pct')
            if not finite_number(net_return) and finite_number(result.get('entry_price')) and finite_number(result.get('exit_price')):
                paid=friction(result['entry_price'],True);net_return=(friction(result['exit_price'],False)/paid-1)*100
                result['alpha_net_return_pct']=round(net_return,4)
            if start and end and finite_number(net_return):
                result['benchmark_return_pct']=round((end/start-1)*100,4)
                result['alpha_pct']=round(net_return-result['benchmark_return_pct'],4)
    forecasts=saved_forecasts()
    # Only the first published stock version for each research date enters archived statistics.
    for items,clock,field in [(rows,time(9),'entry_date')]:
        first={}
        for row in items:
            key=(row['created_at'],row['report_id']);first[row['basis_date']]=min(first.get(row['basis_date'],key),key)
        for row in items:
            stamp=row.get('rule_set_at') or row['created_at']
            row['timely']=bool(row.get(field)) and stamp_before(stamp,row[field],clock)
            row['primary']=True if field=='entry_date' else (row['created_at'],row['report_id'])==first[row['basis_date']]
            row['eligible']=row['timely'] and row['primary']
    return rows,forecasts

def futures_report():
    """Return only the active futures journal; stock backtest history stays archived locally."""
    forecasts=saved_forecasts();scored=[r['result'] for r in forecasts if r['eligible'] and r['result'].get('status')=='scored']
    close_scored=[r for r in scored if finite_number(r.get('close_absolute_error'))]
    directions=[r.get('direction_correct') for r in scored if isinstance(r.get('direction_correct'),bool)]
    latest_model=None
    with research.store() as c:
        init_futures(c);model_row=c.execute('SELECT payload FROM bt_model_runs ORDER BY basis_date DESC,created_at DESC LIMIT 1').fetchone()
        if model_row:
            payload=json.loads(model_row[0]);latest_model={'futures':payload.get('futures'),'version':payload.get('version'),'basis_date':payload.get('basis_date')}
    return {'forecasts':forecasts,'model':latest_model,'job':job(),'version':VERSION,
            'method':'只保存每個研究日的台指期日盤開盤、收盤與支撐壓力預測；首版盤前預測納入誤差統計，重產版本保留但不計入。',
            'summary':{'forecasts':len(scored),'mae':round(sum(r['absolute_error'] for r in scored)/len(scored),2) if scored else None,
                       'bias':round(sum(r['open_error'] for r in scored)/len(scored),2) if scored else None,
                       'close_mae':round(sum(r['close_absolute_error'] for r in close_scored)/len(close_scored),2) if close_scored else None,
                       'close_bias':round(sum(r['close_error'] for r in close_scored)/len(close_scored),2) if close_scored else None,
                       'direction_rate':round(sum(directions)*100/len(directions),2) if directions else None,
                       'range_rate':round(sum(r['inside_outer'] for r in scored)*100/len(scored),2) if scored else None}}

def report():
    rows,forecasts=saved_rows();scored=[r['result'] for r in rows if r['eligible'] and r['result'].get('status')=='closed'];open_rows=[r['result'] for r in rows if r['eligible'] and r['result'].get('status')=='open'];fs=[r['result'] for r in forecasts if r['eligible'] and r['result'].get('status')=='scored']
    actual=actual_positions();actual_values=[r['unrealized_pnl'] for r in actual if r.get('unrealized_pnl') is not None]
    net=lambda r:r.get('net_pnl') if r.get('net_pnl') is not None else r.get('pnl',0)
    net_return=lambda r:r.get('net_return_pct') if r.get('net_return_pct') is not None else r.get('return_pct')
    pnl=sum(net(r) for r in scored);capital=sum(r.get('capital') or r['entry_price']*1000 for r in scored)
    wins=[r for r in scored if net(r)>0];losses=[r for r in scored if net(r)<=0];multiples=[r.get('r_multiple') for r in scored if finite_number(r.get('r_multiple'))]
    curve=[];running=0
    for r in sorted(scored,key=lambda r:r['exit_date']):
        running+=net(r)
        if curve and curve[-1]['date']==r['exit_date']:curve[-1]['pnl']=round(running,2)
        else:curve.append({'date':r['exit_date'],'pnl':round(running,2)})
    expected_pct=round(sum(net_return(r) for r in scored if finite_number(net_return(r)))/len([r for r in scored if finite_number(net_return(r))]),4) if any(finite_number(net_return(r)) for r in scored) else None
    alphas=[r.get('alpha_pct') for r in scored if finite_number(r.get('alpha_pct'))]
    close_scored=[r for r in fs if finite_number(r.get('close_absolute_error'))]
    directions=[r.get('direction_correct') for r in fs if isinstance(r.get('direction_correct'),bool)]
    latest_model=None
    with research.store() as c:
        init(c);model_row=c.execute('SELECT payload FROM bt_model_runs ORDER BY basis_date DESC,created_at DESC LIMIT 1').fetchone()
        if model_row:latest_model=json.loads(model_row[0])
    return {'rows':rows,'forecasts':forecasts,'model':latest_model,'actual_positions':actual,'actual_summary':{'count':len(actual),'cost_value':round(sum(r['cost_value'] for r in actual),2),'market_value':round(sum(r['market_value'] or 0 for r in actual),2),'unrealized_pnl':round(sum(actual_values),2),'needs_action':sum(r['action_tone'] in ('danger','profit','watch') for r in actual)},'job':job(),'method':METHOD,'version':VERSION,'curve':curve,'summary':{'closed':len(scored),'wins':len(wins),'win_rate':round(len(wins)/len(scored)*100,2) if scored else None,'realized_pnl':round(pnl,2),'unrealized_pnl':round(sum((r.get('unrealized_pnl') or 0) for r in open_rows),2),'return_pct':round(pnl/capital*100,4) if capital else None,'expected_value_pct':expected_pct,'expected_r':round(sum(multiples)/len(multiples),4) if multiples else None,'avg_win_r':round(sum(r['r_multiple'] for r in wins if finite_number(r.get('r_multiple')))/len([r for r in wins if finite_number(r.get('r_multiple'))]),4) if any(finite_number(r.get('r_multiple')) for r in wins) else None,'avg_loss_r':round(sum(r['r_multiple'] for r in losses if finite_number(r.get('r_multiple')))/len([r for r in losses if finite_number(r.get('r_multiple'))]),4) if any(finite_number(r.get('r_multiple')) for r in losses) else None,'alpha_samples':len(alphas),'alpha_pct':round(sum(alphas)/len(alphas),4) if alphas else None,'positive_alpha_rate':round(sum(v>0 for v in alphas)*100/len(alphas),2) if alphas else None,'open':len(open_rows),'forecasts':len(fs),'mae':round(sum(r['absolute_error'] for r in fs)/len(fs),2) if fs else None,'bias':round(sum(r['open_error'] for r in fs)/len(fs),2) if fs else None,'close_mae':round(sum(r['close_absolute_error'] for r in close_scored)/len(close_scored),2) if close_scored else None,'close_bias':round(sum(r['close_error'] for r in close_scored)/len(close_scored),2) if close_scored else None,'direction_rate':round(sum(directions)*100/len(directions),2) if directions else None,'range_rate':round(sum(r['inside_outer'] for r in fs)*100/len(fs),2) if fs else None}}

def finite_number(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def feedback(cutoff,stocks=True):
    forecasts=saved_forecasts();rows=saved_rows()[0] if stocks else []
    prior=[r for r in forecasts if r['eligible'] and r['result'].get('status')=='scored' and r['target_date']<=cutoff]
    # Iterate chronologically, then select the most recent 30 outcomes known by cutoff.
    prior=sorted(prior,key=lambda r:r['target_date'])[-30:]
    closed=[r for r in rows if r['eligible'] and r['result'].get('status')=='closed' and r['result']['exit_date']<=cutoff]
    close_prior=[r for r in prior if finite_number(r['result'].get('close_error'))]
    result={'as_of':cutoff,'method':'只回看截止研究日已揭曉的首版盤前紀錄；模型另以時間順序向前訓練與驗證。','forecast_samples':len(prior),'mean_signed_error':sum(r['result']['open_error'] for r in prior)/len(prior) if prior else None,
            'mean_close_error':sum(r['result']['close_error'] for r in close_prior)/len(close_prior) if close_prior else None,
            'recent_forecasts':[{'date':r['target_date'],'predicted_open':r['prediction']['open'],'actual_open':r['result']['actual']['open'],'open_error':r['result']['open_error'],'predicted_close':r['prediction'].get('close'),'actual_close':r['result']['actual'].get('close'),'close_error':r['result'].get('close_error')} for r in prior[-10:]]}
    if stocks:result.update(stock_closed=len(closed),stock_win_rate=sum((r['result'].get('net_pnl') if r['result'].get('net_pnl') is not None else r['result'].get('pnl',0))>0 for r in closed)/len(closed)*100 if closed else None)
    return result

def prepare(basis):
    # A user-initiated report run also reviews saved predictions before composing new ones.
    refresh_futures(wait=True)
    warnings=[];target=None;bars=[]
    try:target=next_day(basis,fetch=True)
    except Exception:warnings.append('官方交易日曆暫時無法讀取；回測等待手動更新確認買入日')
    try:bars=fetch_futures(basis)
    except Exception:warnings.append('研究日台指期日盤行情缺少，預測點位應留空')
    # Freeze a monthly contract that remains listed after the research date.
    expiry=date.fromisoformat(basis);third=[d for d in calendar.Calendar().itermonthdates(expiry.year,expiry.month) if d.month==expiry.month and d.weekday()==2][2]
    contracts=sorted(r['contract'] for r in bars if r['contract']>=(expiry.strftime('%Y%m') if expiry<third else (expiry.replace(day=28)+timedelta(days=4)).strftime('%Y%m')))
    base=next((r for r in bars if contracts and r['contract']==contracts[0]),None)
    opening={'captured_at':research.now(),'cutoff':'目標日 08:30（台北）','rows':[]}
    now_local=datetime.now(research.TZ)
    if target and now_local.date().isoformat()==target and time(8)<=now_local.time()<time(8,45):
        for symbol,name,source in [('^N225','日經225','https://finance.yahoo.com/quote/%5EN225/'),('^KS11','韓國綜合指數','https://finance.yahoo.com/quote/%5EKS11/')]:
            try:opening['rows'].append(fetch_opening_index(symbol,name,source,target))
            except ValueError as error:warnings.append(name+'開盤位置未取得：'+str(error))
    else:warnings.append('日韓開盤位置只在目標日 08:00–08:44 產生報告時納入，避免事後資料回填')
    opening['rows'].append({'symbol':'STWN&','name':'富台指（SGX FTSE Taiwan）','status':'需由報告網路搜尋核實','source':'https://www.sgx.com/derivatives/delayed-prices-futures'})
    return {'target_date':target,'session':'日盤 08:45','contract':base['contract'] if base else None,'basis':base,'feedback':feedback(basis,stocks=False),'opening_markets':opening,'warnings':warnings}

def fetch_opening_index(symbol,name,source,target):
    response=requests.get('https://query1.finance.yahoo.com/v8/finance/chart/'+symbol,params={'interval':'1m','range':'1d'},headers={'User-Agent':'branch-monitor/1.0'},timeout=(8,20),allow_redirects=False)
    if response.status_code!=200:raise ValueError('行情來源暫時未回應')
    try:
        data=response.json()['chart']['result'][0];meta=data['meta'];quote=data['indicators']['quote'][0]
        stamps=data['timestamp'];values=[(datetime.fromtimestamp(t,research.TZ),v) for t,v in zip(stamps,quote['close']) if finite_number(v)]
        values=[(stamp,v) for stamp,v in values if stamp.date().isoformat()==target]
        op=next(v for v in quote['open'] if finite_number(v));last=values[-1][1];previous=meta.get('chartPreviousClose') or meta.get('previousClose')
        if not all(number(x) for x in [op,last,previous]):raise ValueError
        return {'symbol':symbol,'name':name,'date':target,'open':op,'last':last,'previous_close':previous,'gap_pct':round((op/previous-1)*100,4),'from_open_pct':round((last/op-1)*100,4),'observed_at':values[-1][0].isoformat(),'source':source}
    except (KeyError,IndexError,StopIteration,TypeError,ValueError):raise ValueError('缺少目標日可驗證開盤行情') from None

def validate_futures_tracking(data,evidence):
    if not isinstance(data,dict):raise ValueError('台指預測結構格式不完整，請手動重新產生報告')
    f=data.get('futures');execution=evidence.get('execution',{});keys=['support2','support1','open','resistance1','resistance2']
    if not isinstance(f,dict) or not isinstance(f.get('reason'),str):raise ValueError('台指預測格式不完整')
    vals=[f.get(k) for k in keys]
    if not all(v is None for v in vals):
        if not execution.get('basis') or not all(number(v) for v in vals) or not all(a<b for a,b in zip(vals,vals[1:])):raise ValueError('台指預測須有同契約行情基準，且支撐2 < 支撐1 < 開盤 < 壓力1 < 壓力2')
    version=(evidence.get('generation_policy') or {}).get('version')
    close=f.get('close')
    if version in {'research-v2','research-futures-v3','research-market-v4'} and not all(v is None for v in vals) and not number(close):raise ValueError('台指預測須包含下一交易日日盤收盤預測')
    if close is not None and not number(close):raise ValueError('台指收盤預測須為有效數字')
    frozen={**{k:f.get(k) for k in keys},'close':close,'reason':f['reason'][:4000],'contract':execution.get('contract'),'session':'日盤 08:45–13:45','basis':execution.get('basis'),'feedback':execution.get('feedback',{}),'opening_markets':execution.get('opening_markets',{})}
    if evidence.get('alpha_model'):
        from . import alpha_model
        frozen=alpha_model.calibrate_futures(frozen,evidence)
    return {'futures':frozen,'version':VERSION}


def validate_tracking(data,evidence):
    candidates={r['symbol']:r for r in evidence.get('candidates',[])};seen=set();picks=[]
    if not isinstance(data,dict):raise ValueError('台指預測結構格式不完整，請手動重新產生報告')
    stock_tracking=(evidence.get('generation_policy') or {}).get('stock_tracking',True)
    observations=data.get('observations',[]) if not stock_tracking else data.get('observations')
    if not isinstance(observations,list):raise ValueError('回測觀察名單格式不完整，請手動重新產生報告')
    technical_policy=evidence.get('technical_policy') if isinstance(evidence.get('technical_policy'),dict) else None
    eligible={symbol for symbol,row in candidates.items() if isinstance(row.get('technical'),dict) and row['technical'].get('dmi_pass') is True}
    levels={r['symbol']:r for r in evidence.get('box_strategy',{}).get('levels',[]) if isinstance(r,dict) and r.get('symbol')}
    for row in observations if stock_tracking else []:
        if not isinstance(row,dict) or row.get('symbol') not in candidates or row['symbol'] in seen:raise ValueError('回測名單包含無效或重複個股')
        symbol=row['symbol'];seen.add(symbol);base=candidates[symbol];tp=row.get('take_profit');sl=row.get('stop_loss');reason=row.get('reason');reference=base.get('close')
        if technical_policy and symbol not in eligible:raise ValueError('回測名單包含DMI未轉多或資料不足個股')
        if not isinstance(reason,str) or not reason.strip():raise ValueError('個股觀察理由缺少')
        if not number(tp) or not number(sl) or not number(reference) or not sl<reference<tp:raise ValueError('停利停損必須滿足：停損 < 研究日收盤 < 停利')
        bottom=levels.get(symbol,{}).get('box_bottom');bottom=bottom if number(bottom) else None
        technical=base.get('technical') if isinstance(base.get('technical'),dict) else None
        picks.append({'symbol':symbol,'name':base.get('name',symbol),'take_profit':tp,'stop_loss':sl,'box_bottom':bottom,'technical':technical,'reason':reason.strip()[:4000]})
    if stock_tracking and not picks and (not technical_policy or eligible):raise ValueError('本次未產生可記錄的個股觀察建議')
    f=data.get('futures');execution=evidence.get('execution',{});keys=['support2','support1','open','resistance1','resistance2']
    if not isinstance(f,dict) or not isinstance(f.get('reason'),str):raise ValueError('台指預測格式不完整')
    vals=[f.get(k) for k in keys]
    if not all(v is None for v in vals):
        if not execution.get('basis') or not all(number(v) for v in vals) or not all(a<b for a,b in zip(vals,vals[1:])):raise ValueError('台指預測須有同契約行情基準，且支撐2 < 支撐1 < 開盤 < 壓力1 < 壓力2')
    version=(evidence.get('generation_policy') or {}).get('version')
    v2=version in {'research-v2','research-futures-v3','research-market-v4'}
    close=f.get('close')
    if v2 and not all(v is None for v in vals) and not number(close):raise ValueError('V2 台指預測須包含下一交易日日盤收盤預測')
    if close is not None and not number(close):raise ValueError('台指收盤預測須為有效數字')
    frozen={**{k:f.get(k) for k in keys},'close':close,'reason':f['reason'][:4000],'contract':execution.get('contract'),'session':'日盤 08:45–13:45','basis':execution.get('basis'),'feedback':execution.get('feedback',{}),'opening_markets':execution.get('opening_markets',{})}
    if evidence.get('alpha_model'):
        from . import alpha_model
        frozen=alpha_model.calibrate_futures(frozen,evidence)
    return {'observations':picks,'futures':frozen,'version':VERSION}

def set_rules(values):
    ident=values.get('id');tp=values.get('take_profit');sl=values.get('stop_loss')
    if not isinstance(ident,str) or not number(tp) or not number(sl) or not sl<tp:raise ValueError('請填有效停利與停損價格，停損需小於停利')
    with research.store() as c:
        init(c);row=c.execute('SELECT * FROM bt_observations WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('找不到觀察紀錄')
        if row['rule_set_at']:raise ValueError('規則已凍結；請於新研究報告建立新版本')
        c.execute('UPDATE bt_observations SET take_profit=?,stop_loss=?,rule_set_at=? WHERE id=?',(tp,sl,research.now(),ident))
    recalculate();return {'saved':True}

def set_actual_position(values):
    ident=values.get('id');buy_price=values.get('buy_price');shares=values.get('shares');buy_date=values.get('buy_date')
    if not isinstance(ident,str) or not number(buy_price) or isinstance(shares,bool) or not isinstance(shares,int) or not 1<=shares<=100000000:raise ValueError('請填有效買入價格與股數；股數需為正整數')
    try:parsed=date.fromisoformat(buy_date)
    except (TypeError,ValueError):raise ValueError('請填有效實際買入日') from None
    if parsed>datetime.now(research.TZ).date():raise ValueError('實際買入日不可晚於今天')
    with research.store() as c:
        init(c);row=c.execute('SELECT o.*,b.payload AS box_payload,n.payload AS recommendation_payload FROM bt_observations o LEFT JOIN bt_box_rules b ON b.id=o.id LEFT JOIN bt_recommendation_rules n ON n.id=o.id WHERE o.id=?',(ident,)).fetchone()
        if not row:raise ValueError('找不到選股紀錄')
        row=dict(row);payload=row.get('recommendation_payload') or row.get('box_payload');box_bottom=(json.loads(payload).get('box_bottom') if payload else None)
        c.execute('INSERT OR REPLACE INTO bt_actual_positions VALUES (?,?,?,?,?,?,?,?,?,?)',(row['symbol'],ident,row['name'],buy_date,buy_price,shares,row['take_profit'],row['stop_loss'],box_bottom,research.now()))
    return {'saved':True,'symbol':row['symbol']}

def delete_actual_position(values):
    symbol=values.get('symbol')
    if not isinstance(symbol,str) or not re.fullmatch(r'\d{4}',symbol):raise ValueError('股票代碼無效')
    with research.store() as c:
        init(c);deleted=c.execute('DELETE FROM bt_actual_positions WHERE symbol=?',(symbol,)).rowcount
    if not deleted:raise ValueError('找不到實際持股')
    return {'deleted':True,'symbol':symbol}

def actual_positions():
    with research.store() as c:
        init(c);positions=[dict(r) for r in c.execute('SELECT * FROM bt_actual_positions ORDER BY buy_date,symbol')]
        for row in positions:
            quote_row=c.execute('SELECT date,payload FROM bt_prices WHERE symbol=? AND date>=? ORDER BY date DESC LIMIT 1',(row['symbol'],row['buy_date'])).fetchone()
            quote=json.loads(quote_row['payload']) if quote_row else None;close=quote.get('close') if quote else None;tp=row.get('take_profit');sl=row.get('stop_loss');action='等待最新收盤';tone='pending'
            if number(close):
                if number(sl) and close<=sl:action='收盤已達停損，建議檢視出場';tone='danger'
                elif number(tp) and close>=tp:action='收盤已達停利，可分批調節獲利';tone='profit'
                elif number(sl) and quote.get('low')<=sl:action='盤中觸及停損，請確認是否已執行';tone='danger'
                elif number(tp) and quote.get('high')>=tp:action='盤中觸及停利，請確認是否已調節';tone='profit'
                elif number(tp) and close>=tp*.98:action='接近停利，準備分批調節';tone='watch'
                elif number(sl) and close<=sl*1.02:action='接近停損，留意風險';tone='danger'
                elif number(row.get('box_bottom')) and close<row['box_bottom']:action='收盤跌破箱底參考，建議重新檢視';tone='watch'
                else:action='尚未觸及規則，續抱觀察';tone='hold'
            cost=row['buy_price']*row['shares'];value=close*row['shares'] if number(close) else None;pnl=value-cost if value is not None else None
            initial_risk=(row['buy_price']-sl)*row['shares'] if number(sl) and row['buy_price']>sl else None
            row.update({'mark_date':quote_row['date'] if quote_row else None,'latest':quote,'latest_close':close,'cost_value':round(cost,2),'market_value':round(value,2) if value is not None else None,'unrealized_pnl':round(pnl,2) if pnl is not None else None,'return_pct':round(pnl/cost*100,4) if pnl is not None and cost else None,'r_multiple':round(pnl/initial_risk,4) if pnl is not None and initial_risk else None,'action':action,'action_tone':tone})
    return positions

def recalculate(stocks=True):
    with research.store() as c:
        init(c);days=[r[0] for r in c.execute('SELECT date FROM bt_sessions ORDER BY date')];as_of=days[-1] if days else ''
        if days:
            first=date.fromisoformat(days[0]);last=date.fromisoformat(as_of);observed=set(days)
            while first<=last:
                key=first.strftime('%Y-%m');verified=c.execute('SELECT payload FROM bt_meta WHERE key=?',('month:'+key,)).fetchone()
                if not verified and first.weekday()<5:observed.add(first.isoformat())
                first+=timedelta(days=1)
            days=sorted(observed)
        for row in ([dict(r) for r in c.execute('SELECT o.*,b.payload AS box_payload,n.payload AS recommendation_payload FROM bt_observations o LEFT JOIN bt_box_rules b ON b.id=o.id LEFT JOIN bt_recommendation_rules n ON n.id=o.id')] if stocks else []):
            quotes={r[0]:json.loads(r[1]) for r in c.execute('SELECT date,payload FROM bt_prices WHERE symbol=?',(row['symbol'],))}
            if row.get('recommendation_payload'):outcome=evaluate_recommendation(row,days,quotes,as_of)
            elif row.get('box_payload'):outcome=evaluate_box(row,json.loads(row['box_payload']),days,quotes,as_of)
            else:outcome=evaluate(row,days,quotes,as_of)
            c.execute('INSERT OR REPLACE INTO bt_results VALUES (?,?,?)',(row['id'],json.dumps(outcome),research.now()))
        for row in [dict(r) for r in c.execute('SELECT * FROM bt_predictions')]:
            contract=json.loads(row['payload']).get('contract');r=c.execute('SELECT payload FROM bt_futures WHERE date=? AND contract=?',(row['target_date'],contract)).fetchone()
            c.execute('INSERT OR REPLACE INTO bt_future_results VALUES (?,?,?)',(row['report_id'],json.dumps(prediction_result(row,json.loads(r[0]) if r else None)),research.now()))

def recalculate_futures():
    with research.store() as c:
        init_futures(c)
        for row in [dict(r) for r in c.execute('SELECT * FROM bt_predictions')]:
            contract=json.loads(row['payload']).get('contract')
            quote_row=c.execute('SELECT payload FROM bt_futures WHERE date=? AND contract=?',(row['target_date'],contract)).fetchone()
            outcome=prediction_result(row,json.loads(quote_row[0]) if quote_row else None)
            c.execute('INSERT OR REPLACE INTO bt_future_results VALUES (?,?,?)',(row['report_id'],json.dumps(outcome),research.now()))


def local_quotes(day,symbols):
    result={}
    for connect,table in [(research.main_db,'prices'),(sector_flow.connect,'own_prices')]:
        try:
            c=connect()
            try:
                for r in c.execute(f'SELECT * FROM {table} WHERE date=?',(day,)):
                    row=dict(r)
                    if row['symbol'] in symbols and valid_bar(row) and row['symbol'] not in result:result[row['symbol']]=row
            finally:c.close()
        except Exception:pass
    return result

def refresh_futures(wait=False):
    """Update only saved TX forecasts; archived stock tables are never read."""
    if not _lock.acquire(False):return {'busy':True}
    try:
        with research.store() as c:
            init_futures(c);set_meta(c,'job',{'status':'running','message':'正在更新已保存的台指日盤預測','started_at':research.now()})
        def worker():
            errors=[]
            try:
                forecasts=saved_forecasts();now_local=datetime.now(research.TZ);cutoff=(now_local.date() if now_local.time()>=time(16) else now_local.date()-timedelta(days=1)).isoformat()
                for basis in sorted({r['basis_date'] for r in forecasts}):
                    try:
                        target=next_day(basis,fetch=True)
                        with research.store() as c:c.execute('UPDATE bt_predictions SET target_date=? WHERE basis_date=? AND target_date IS NULL',(target,basis))
                    except Exception:errors.append(basis+'：交易日曆未就緒')
                forecasts=saved_forecasts()
                for day in sorted({r['target_date'] for r in forecasts if r.get('target_date') and r['target_date']<=cutoff})[:40]:
                    try:fetch_futures(day)
                    except Exception:errors.append(day+'：台指日盤未就緒')
                recalculate_futures()
                with research.store() as c:set_meta(c,'job',{'status':'partial' if errors else 'complete','message':f'台指預測已更新；{len(errors)} 項缺口待手動重試' if errors else '台指預測已更新，尚未到期的紀錄會繼續等待行情','errors':errors[:80],'finished_at':research.now()})
            except Exception:
                with research.store() as c:set_meta(c,'job',{'status':'failed','message':'台指更新失敗；已保存預測保留，請手動重試','finished_at':research.now()})
            finally:_lock.release()
        if wait:worker()
        else:threading.Thread(target=worker,daemon=True).start()
    except Exception:
        _lock.release();raise
    return {'started':True}


def refresh(wait=False,stocks=True):
    if not _lock.acquire(False):return {'busy':True}
    try:
        migrate()
        with research.store() as c:set_meta(c,'job',{'status':'running','message':'正在補齊回測所需行情，保留既有報告與預測','started_at':research.now()})
        def worker():
            errors=[];queried=0
            try:
                rows,forecasts=saved_rows();rows=rows if stocks else [];today=datetime.now(research.TZ);cutoff=(today.date() if today.time()>=time(16) else today.date()-timedelta(days=1)).isoformat()
                bases=sorted({r['basis_date'] for r in rows+forecasts})
                for basis in bases:
                    try:
                        day=next_day(basis,fetch=True)
                        with research.store() as c:
                            if stocks:c.execute('UPDATE bt_observations SET entry_date=? WHERE basis_date=? AND entry_date IS NULL',(day,basis))
                            c.execute('UPDATE bt_predictions SET target_date=? WHERE basis_date=? AND target_date IS NULL',(day,basis))
                    except Exception:errors.append(basis+'：交易日曆未就緒')
                rows,forecasts=saved_rows()
                if bases:
                    month=date.fromisoformat(bases[0]).replace(day=1);end=date.fromisoformat(cutoff)
                    while month<=end:
                        try:
                            days=rs.index_month(month.isoformat(),refresh=month.year==end.year and month.month==end.month)
                            with research.store() as c:
                                c.executemany('INSERT OR IGNORE INTO bt_sessions VALUES (?)',[(d,) for d in days if d<=cutoff])
                                set_meta(c,'month:'+month.strftime('%Y-%m'),{'as_of':max(days) if days else None})
                        except Exception:errors.append(month.isoformat()+'：官方交易日資料未就緒')
                        month=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
                    with research.store() as c:days=[r[0] for r in c.execute('SELECT date FROM bt_sessions WHERE date>? AND date<=? ORDER BY date',(bases[0],cutoff))]
                    for day in days:
                        wanted={r['symbol'] for r in rows if r.get('entry_date') and r['entry_date']<=day and r.get('rule_set_at') and (r['result'].get('status')!='closed' or r['result']['exit_date']>=day)}
                        with research.store() as c:cached={r[0] for r in c.execute('SELECT symbol FROM bt_prices WHERE date=?',(day,))}
                        missing=wanted-cached
                        if not missing:continue
                        local=local_quotes(day,missing)
                        if missing-local.keys() and queried<40:
                            for market in ['TWSE','TPEX']:
                                try:
                                    queried+=1
                                    for r in sources.market_prices(day,market):
                                        if r['symbol'] in missing and valid_bar(r):local[r['symbol']]=r
                                except Exception:errors.append(day+' '+market+'：開高低收資料未就緒')
                        with research.store() as c:c.executemany('INSERT OR REPLACE INTO bt_prices VALUES (?,?,?)',[(day,s,json.dumps(r)) for s,r in local.items()])
                        if missing-local.keys():errors.append(day+'：仍缺 '+','.join(sorted(missing-local.keys())))
                    if stocks and days:
                        latest_day=days[-1]
                        with research.store() as c:
                            actual_symbols={r[0] for r in c.execute('SELECT symbol FROM bt_actual_positions')};cached={r[0] for r in c.execute('SELECT symbol FROM bt_prices WHERE date=?',(latest_day,))}
                        missing=actual_symbols-cached;local=local_quotes(latest_day,missing)
                        for market in ['TWSE','TPEX']:
                            if not missing-local.keys():break
                            try:
                                queried+=1
                                for r in sources.market_prices(latest_day,market):
                                    if r['symbol'] in missing and valid_bar(r):local[r['symbol']]=r
                            except Exception:errors.append(latest_day+' '+market+'：實際持股收盤資料未就緒')
                        with research.store() as c:c.executemany('INSERT OR REPLACE INTO bt_prices VALUES (?,?,?)',[(latest_day,s,json.dumps(r)) for s,r in local.items()])
                        if missing-local.keys():errors.append(latest_day+'：實際持股仍缺 '+','.join(sorted(missing-local.keys())))
                for day in sorted({r['target_date'] for r in forecasts if r.get('target_date') and r['target_date']<=cutoff})[:40]:
                    try:fetch_futures(day)
                    except Exception:errors.append(day+'：台指日盤未就緒')
                recalculate(stocks=stocks)
                label='台指預測'
                with research.store() as c:set_meta(c,'job',{'status':'partial' if errors else 'complete','message':f'{label}已更新；{len(errors)} 項缺口待手動重試' if errors else f'{label}已更新，尚未到期的紀錄會繼續等待行情','errors':errors[:80],'finished_at':research.now()})
            except Exception:
                with research.store() as c:set_meta(c,'job',{'status':'failed','message':'更新失敗；已保存資料保留，請手動重試','finished_at':research.now()})
            finally:_lock.release()
        if wait:worker()
        else:threading.Thread(target=worker,daemon=True).start()
    except Exception:
        _lock.release();raise
    return {'started':True}
