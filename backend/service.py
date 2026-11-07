from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import active_etf, db, sources, major, broker_focus, sync_audit, warrants
from .analytics import analyze
from .coverage import merge_ranges, missing_ranges

TZ = ZoneInfo('Asia/Taipei')
_sync_lock = threading.Lock()


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def today():
    return datetime.now(TZ).date()


def branches():
    with db.connect() as conn:
        selected = [dict(r) for r in conn.execute('SELECT * FROM branches WHERE active=1 ORDER BY name')]
        row = conn.execute("SELECT value FROM settings WHERE key='watchlist_order'").fetchone()
    order = json.loads(row['value']) if row else []
    positions = {key:i for i,key in enumerate(order)}
    return sorted(selected,key=lambda b:positions.get(b['broker']+':'+b['branch'],len(order)))


def add_branch(broker, branch):
    if not _sync_lock.acquire(blocking=False):
        raise ValueError('資料同步中，請等待完成後再加入分點')
    try:
        existing=next((b for b in branches() if b['broker']==broker and b['branch']==branch),None)
        if existing:
            return existing
        selected=next((b for b in sources.branch_catalog() if b['broker']==broker and b['branch']==branch),None)
        if not selected:
            raise ValueError('請從來源清單選擇有效分點')
        order=[b['broker']+':'+b['branch'] for b in branches()]+[broker+':'+branch]
        with db.connect() as conn:
            conn.execute('INSERT INTO branches VALUES (?,?,?,1) ON CONFLICT(broker,branch) DO UPDATE SET name=excluded.name,active=1',
                         (broker,branch,selected['name']))
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('watchlist_order',json.dumps(order)))
            conn.execute("DELETE FROM settings WHERE key='last_sync_date'")
        return selected
    finally:
        _sync_lock.release()


def replace_watchlist(requested):
    """Atomically change only the explicit watchlist; preserve all downloaded history."""
    if not isinstance(requested,list) or not 1<=len(requested)<=100:
        raise ValueError('追蹤名單需包含 1～100 個明確分點')
    if not _sync_lock.acquire(blocking=False):
        raise ValueError('資料同步中，請等待完成後再調整分點名單')
    try:
        catalog={(b['broker'],b['branch']):b for b in sources.branch_catalog()}
        selected=[]
        for item in requested:
            if not isinstance(item,dict):
                raise ValueError('分點格式不符')
            key=(item.get('broker'),item.get('branch'))
            if not all(isinstance(v,str) for v in key) or key not in catalog:
                raise ValueError('名單包含無效的券商或分點代碼')
            if any((b['broker'],b['branch'])==key for b in selected):
                raise ValueError('追蹤名單中有重複分點')
            selected.append(catalog[key])
        with db.connect() as conn:
            conn.execute('UPDATE branches SET active=0')
            conn.executemany('INSERT INTO branches VALUES (?,?,?,1) ON CONFLICT(broker,branch) DO UPDATE SET name=excluded.name,active=1',
                [(b['broker'],b['branch'],b['name']) for b in selected])
            conn.execute("DELETE FROM settings WHERE key='last_sync_date'")
            conn.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                ('watchlist_order',json.dumps([b['broker']+':'+b['branch'] for b in selected])))
        return selected
    finally:
        _sync_lock.release()


def job_status():
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM jobs ORDER BY started_at DESC, rowid DESC LIMIT 1').fetchone()
    if not row:
        return None
    result = dict(row)
    result['errors'] = json.loads(result['errors'])
    result['scope'] = json.loads(result.get('scope','[]'))
    try:result['progress']=json.loads(result.get('progress') or '{}')
    except (TypeError,json.JSONDecodeError):result['progress']={}
    # Older completed partial jobs said "待重試", which looked like an active
    # operation in the UI. Keep the stored audit record, but present its state
    # explicitly as finished.
    if result['status']=='partial' and result.get('finished_at'):
        result['message']=re.sub(r'；(\d+) 項待重試$',r'；回補已結束，\1 項仍未補齊',result['message'])
    return result


def save_flows(conn, broker, branch, rows):
    conn.executemany('INSERT OR REPLACE INTO flows VALUES (?,?,?,?,?,?,?,?,?)', [
        (broker,branch,r['date'],r['symbol'],r['name'],r['buy'],r['sell'],r['net'],r['source']) for r in rows])


def save_ranking_entries(conn,broker,branch,day,unit,rows):
    """Preserve daily ranking membership independently from detailed history flows."""
    conn.execute('DELETE FROM ranking_entries WHERE broker=? AND branch=? AND date=? AND unit=?',(broker,branch,day,unit))
    conn.executemany('INSERT INTO ranking_entries VALUES (?,?,?,?,?,?,?,?,?,?)',[
        (broker,branch,day,unit,r['symbol'],r['name'],r['buy'],r['sell'],r['net'],r['source']) for r in rows])


def validate_history_start(value):
    if value is None or value=='':return None
    if not isinstance(value,str):raise ValueError('固定回補起日需為 YYYY-MM-DD')
    try:parsed=date.fromisoformat(value)
    except ValueError:raise ValueError('固定回補起日需為有效的 YYYY-MM-DD')
    if parsed.isoformat()!=value or parsed>today() or parsed<date(2000,1,1):
        raise ValueError('固定回補起日需介於 2000-01-01 與今天之間')
    return value


def sync(days=45, targets=None, force=False, start_date=None):
    if not isinstance(days,int) or isinstance(days,bool) or not 7<=days<=180:
        raise ValueError('回補範圍需為 7～180 個日曆日')
    if not isinstance(force,bool):
        raise ValueError('force 需為布林值')
    # Keep validating the legacy arguments because older clients may still send
    # them, but daily sync deliberately does not use a historical start date.
    # Historical gaps are handled by the separate repair workflow.
    validate_history_start(start_date)
    requested=None
    if targets is not None:
        if not isinstance(targets,list) or not 1<=len(targets)<=100:
            raise ValueError('targets 必須包含明確分點，不能是空名單')
        requested=[]
        for item in targets:
            if not isinstance(item,dict) or not all(isinstance(item.get(k),str) for k in ['broker','branch']):
                raise ValueError('targets 分點代碼需為字串')
            key=(item['broker'],item['branch'])
            if key in requested:
                raise ValueError('targets 含重複分點')
            requested.append(key)
    if not _sync_lock.acquire(blocking=False):
        running=job_status()
        if requested is not None and set(requested)!={(b['broker'],b['branch']) for b in (running or {}).get('scope',[])}:
            raise ValueError('另一個範圍正在同步，這次單家回補尚未開始')
        return running
    try:
        watched=branches()
        available={(b['broker'],b['branch']):b for b in watched}
        if requested is not None and any(key not in available for key in requested):
            raise ValueError('只能同步已加入追蹤的分點')
        active=[available[key] for key in requested] if requested is not None else watched
        if not active:
            raise ValueError('請先加入要追蹤的分點')
        entire=set((b['broker'],b['branch']) for b in active)==set(available)
        job_id=uuid.uuid4().hex
        label=active[0]['name'] if len(active)==1 else f'{len(active)} 個追蹤分點'
        with db.connect() as conn:
            conn.execute('INSERT INTO jobs(id,started_at,status,message,scope) VALUES (?,?,?,?,?)',
                         (job_id,now(),'running',f'{label} · {"重新核對最新交易日" if force else "每日更新，只延伸最新尾端資料"}',json.dumps(active,ensure_ascii=False)))
        threading.Thread(target=_run_sync,args=(job_id,active,force,entire),daemon=True).start()
    except Exception:
        _sync_lock.release()
        raise
    return job_status()


def _store_history(broker,branch,symbol,start,end,rows):
    """Commit one verified gap without widening coverage over any failed gaps."""
    with db.connect() as conn:
        observed=conn.execute("SELECT date FROM flows WHERE broker=? AND branch=? AND symbol=? AND date>=? AND date<=? AND source LIKE '%/zgb0.djhtm%' AND (buy>0 OR sell>0)",
                              (broker,branch,symbol,start,end)).fetchall()
        returned={r['date'] for r in rows}
        if any(r['date'] not in returned for r in observed):
            raise sources.SourceError('個股明細缺少已觀察榜單交易；保留資料等待來源更新')
        existing=[dict(r) for r in conn.execute('SELECT start,end FROM history_intervals WHERE broker=? AND branch=? AND symbol=?',(broker,branch,symbol))]
        merged=merge_ranges(existing+[{'start':start,'end':end}])
        conn.execute('DELETE FROM flows WHERE broker=? AND branch=? AND symbol=? AND date>=? AND date<=?',(broker,branch,symbol,start,end))
        save_flows(conn,broker,branch,rows)
        conn.execute('DELETE FROM history_intervals WHERE broker=? AND branch=? AND symbol=?',(broker,branch,symbol))
        conn.executemany('INSERT INTO history_intervals VALUES (?,?,?,?,?,?)',
                         [(broker,branch,symbol,r['start'],r['end'],now()) for r in merged])
        # Compatibility summary represents one contiguous interval, never an outer envelope with holes.
        newest=merged[-1]
        conn.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)',(broker,branch,symbol,newest['start'],newest['end'],now()))


def _weekdays_after(last_day,latest):
    """Return weekday suffix after last_day; never inspect an older internal gap."""
    finish=date.fromisoformat(latest)
    current=finish if not last_day else date.fromisoformat(last_day)+timedelta(days=1)
    return [(current+timedelta(days=i)).isoformat() for i in range((finish-current).days+1)
            if (current+timedelta(days=i)).weekday()<5] if current<=finish else []


def latest_ranking_probe(branch):
    """Default MoneyDJ pages may lag their explicitly dated daily pages."""
    latest,rows=sources.ranking(branch['broker'],branch['branch'],refresh=True)
    with db.connect() as c:
        candidate=c.execute("""SELECT MAX(m.date) FROM market_days m JOIN prices p ON p.date=m.date
            WHERE m.twse_status='ok' AND m.tpex_status='ok' AND p.symbol='TAIEX'
            AND p.close>0 AND m.date<=?""",(today().isoformat(),)).fetchone()[0]
    if candidate and candidate>latest:
        try:
            day,dated=sources.ranking(branch['broker'],branch['branch'],candidate,refresh=True)
            if dated and day==candidate:return day,dated
        except sources.SourceError:
            pass  # Unpublished/empty dated pages never certify a newer daily snapshot.
    return latest,rows


def _run_sync(job_id,active,force=False,entire=True):
    errors=[]
    ignored=db.ignored_symbols()
    done,total,reused,fetched=0,1,0,0
    last_progress=0.0
    started_clock=time.monotonic();rate_points=[(started_clock,0)]
    plan_ready=False;phase='source';current_item=None
    stages={
        'source':{'label':'來源日期','done':0,'total':1},
        'market':{'label':'共用市場行情','done':0,'total':0},
        'ranking':{'label':'分點榜單','done':0,'total':0},
        'history':{'label':'個股明細（按需）','done':0,'total':None},
        'audit':{'label':'本機盤點','done':0,'total':0},
    }

    def progress(message,step=0,always=False,stage=None,item=None):
        nonlocal done,total,last_progress,phase,current_item,rate_points
        if stage:
            phase=stage
            if step:stages[stage]['done']+=step
        current_item=item
        done+=step
        clock=time.monotonic()
        if step:
            rate_points.append((clock,done))
            cutoff=clock-60
            rate_points=[point for point in rate_points if point[0]>=cutoff]
            if len(rate_points)<2:rate_points=[(started_clock,0),(clock,done)]
        if not always and clock-last_progress<.2:
            return
        last_progress=clock
        total=sum(int(row['total'] or 0) for row in stages.values())
        if len(rate_points)>=2 and rate_points[-1][0]>rate_points[0][0]:
            rate=round((rate_points[-1][1]-rate_points[0][1])/(rate_points[-1][0]-rate_points[0][0])*60,1)
        else:rate=None
        remaining=max(0,total-done)
        eta=round(remaining/(rate/60)) if plan_ready and rate and rate>0 else None
        payload={'version':1,'planned':plan_ready,'phase':phase,
                 'phase_label':stages.get(phase,{}).get('label',phase),'rate_per_min':rate,
                 'eta_seconds':eta,'current':current_item,'stages':stages}
        with db.connect() as conn:
            conn.execute('UPDATE jobs SET done=?,total=?,message=?,errors=?,progress=? WHERE id=?',
                (done,total,message,json.dumps(errors,ensure_ascii=False),json.dumps(payload,ensure_ascii=False),job_id))
    try:
        first=active[0]
        progress(f'{first["name"]} · 核對來源最新日期',always=True,stage='source',item={'kind':'source','branch':first['name']})
        latest,probe=latest_ranking_probe(first)
        fetched+=1
        progress(f'來源日期已核對 · {latest}',1,always=True,stage='source',item={'kind':'source','branch':first['name'],'date':latest})
        if latest>today().isoformat():
            raise sources.SourceError('來源日期晚於本機日期')
        with db.connect() as conn:
            last_market=dict(conn.execute("""SELECT
                MAX(CASE WHEN twse_status='ok' THEN date END) AS twse_status,
                MAX(CASE WHEN tpex_status='ok' THEN date END) AS tpex_status
                FROM market_days WHERE date<=?""",(latest,)).fetchone())
            market_states={r['date']:dict(r) for r in conn.execute(
                'SELECT * FROM market_days WHERE date<=? ORDER BY date',(latest,))}
        market_plan=[]
        for market,field in [('TWSE','twse_status'),('TPEX','tpex_status')]:
            planned=[latest] if force else _weekdays_after(last_market.get(field),latest)
            if not planned:reused+=1
            market_plan.extend((day,market,field) for day in planned)
        stages['market']['total']=len(market_plan)
        progress(f'共用行情工作量已建立 · {len(market_plan)} 項',always=True,stage='market')
        for day,market,field in market_plan:
            item={'kind':'market','date':day,'name':market}
            progress(f'補齊共用行情 · {day} {market}',stage='market',item=item)
            state=market_states.setdefault(day,{'date':day,'twse_status':'no_data','tpex_status':'no_data'})
            try:
                fetched+=1
                rows=sources.market_prices(day,market,refresh=True)
                if not rows and state.get(field)=='ok':
                    raise sources.SourceError('已完成的行情重新核對時回傳空資料；保留原有行情')
                state[field]='ok' if rows else 'no_data'
                with db.connect() as conn:
                    if rows:
                        conn.executemany('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?)',[
                            (r['date'],r['symbol'],r['name'],r['close'],r['open'],r['high'],r['low'],r['volume'],r['market']) for r in rows])
            except Exception as error:
                if state.get(field)!='ok':
                    state[field]='error'
                errors.append(f'{day} {market}：{str(error)[:180]}')
            with db.connect() as conn:
                conn.execute('INSERT OR REPLACE INTO market_days VALUES (?,?,?,?)',(day,state['twse_status'],state['tpex_status'],''))
            progress(f'共用行情已處理 · {day} {market}',1,stage='market',item=item)
        trading=[day for day,state in market_states.items() if day<=latest and state.get('twse_status')=='ok']
        for field,label in [('twse_status','上市'),('tpex_status','上櫃')]:
            if market_states.get(latest,{}).get(field) not in ['ok','error']:
                errors.append(f'{latest} {label}行情尚未就緒，尚不能標記整個期間完成')
        if not trading:
            raise sources.SourceError('沒有取得可核對日期的上市行情，請稍後重試')
        snapshot_plan=[]
        for branch in active:
            with db.connect() as conn:
                existing={r['date']:r['status'] for r in conn.execute(
                    'SELECT date,status FROM snapshots WHERE broker=? AND branch=? AND date<=?',
                    (branch['broker'],branch['branch'],latest))}
                latest_units={r['unit'] for r in conn.execute(
                    'SELECT DISTINCT unit FROM ranking_entries WHERE broker=? AND branch=? AND date=?',
                    (branch['broker'],branch['branch'],latest))}
            observed=max((day for day,status in existing.items() if status=='observed'),default=None)
            if force:
                planned=[latest] if latest in trading else []
            elif not observed:
                planned=[latest] if latest in trading else []
            else:
                planned=[day for day in trading if observed<day<=latest]
                if observed==latest and latest in trading and latest_units!={'E','B'}:
                    planned=[latest]
            if not planned:reused+=2
            snapshot_plan.extend((branch,day,existing.get(day)) for day in planned)
        # Latest snapshots first for every target; untouched verified snapshots are not rewritten.
        snapshot_plan.sort(key=lambda item:item[1]!=latest)
        stages['ranking']['total']=len(snapshot_plan)*2
        progress(f'分點榜單工作量已建立 · {len(snapshot_plan)*2} 項',always=True,stage='ranking')
        for branch,day,previous_status in snapshot_plan:
            broker,code=branch['broker'],branch['branch']
            day_errors=[]
            candidates=[]
            lots=None
            for unit in ['E','B']:
                label='張數' if unit=='E' else '金額';item={'kind':'ranking','branch':branch['name'],'date':day,'name':label+'榜單'}
                progress(f'{branch["name"]} · 補齊 {day} {label}榜單',stage='ranking',item=item)
                try:
                    if unit=='E' and day==latest and broker==first['broker'] and code==first['branch']:
                        rows=probe
                    else:
                        fetched+=1
                        _,rows=sources.ranking(broker,code,day,refresh=True,unit=unit)
                    candidates.extend(rows)
                    if unit=='E':lots=rows
                    with db.connect() as conn:save_ranking_entries(conn,broker,code,day,unit,rows)
                except Exception as error:
                    day_errors.append(str(error)[:160])
                    errors.append(f'{day} {branch["name"]} {unit}：{str(error)[:160]}')
                progress(f'{branch["name"]} · {day} {label}榜單已處理',1,stage='ranking',item=item)
            # Failed force requests must not erase a previously verified snapshot.
            if not day_errors or previous_status!='observed':
                with db.connect() as conn:
                    conn.executemany('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?)',[(broker,code,r['symbol'],r['name']) for r in candidates])
                    if lots is not None:
                        # Ranked rows only fill dates without verified per-stock history.
                        # A later failing history refresh must leave old detailed flows intact.
                        uncovered=[r for r in lots if not conn.execute(
                            'SELECT 1 FROM history_intervals WHERE broker=? AND branch=? AND symbol=? AND start<=? AND end>=?',
                            (broker,code,r['symbol'],day,day)).fetchone()]
                        save_flows(conn,broker,code,uncovered)
                    conn.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?)',
                                 (broker,code,day,'error' if day_errors else 'observed',len(lots or []),'；'.join(day_errors),now()))

        dormant_skipped=0;daily_observed=0
        for branch in active:
            broker,code=branch['broker'],branch['branch']
            with db.connect() as conn:
                all_count=conn.execute('SELECT COUNT(*) FROM candidates WHERE broker=? AND branch=?',(broker,code)).fetchone()[0]
                observed={r['symbol'] for r in conn.execute(
                    'SELECT DISTINCT symbol FROM ranking_entries WHERE broker=? AND branch=? AND date=?',
                    (broker,code,latest)) if r['symbol'] not in ignored}
            daily_observed+=len(observed)
            dormant_skipped+=max(0,all_count-len(observed))
        # Daily sync is deliberately ranking-only. The E ranking already contains
        # the day's exact buy/sell lots and was saved above. Historical per-stock
        # pages belong to the explicit repair queue, so one new day never fans out
        # into thousands of requests for old candidates.
        stages['history']['total']=0
        stages['audit']['total']=0 if errors else 1
        plan_ready=True
        progress(f'完整工作量已建立 · 當日榜單 {daily_observed:,} 組直接入庫，歷史候選 {dormant_skipped:,} 組交由缺口佇列分批回補',always=True,stage='history')
        if not errors:
            item={'kind':'audit','name':'重新分類每日與歷史修復範圍'}
            progress('本機盤點 · 重新分類每日與歷史修復範圍',always=True,stage='audit',item=item)
            try:sync_audit.run(20)
            except Exception as error:errors.append('本機活躍集合盤點：'+str(error)[:160])
            progress('本機盤點完成',1,always=True,stage='audit',item=item)
        status='partial' if errors else 'complete'
        label=active[0]['name'] if len(active)==1 else f'{len(active)} 個追蹤分點'
        message=(f'{label} 每日榜單已更新至 {latest}' if not errors else f'{label} 每日榜單更新至 {latest} 尚有缺項')+f'；榜單 {daily_observed} 組直接入庫，歷史候選 {dormant_skipped} 組由缺口佇列分批回補，沿用 {reused} 項已完成資料，新查詢 {fetched} 次'
        if errors:message+=f'；回補已結束，{len(errors)} 項仍未補齊'
        phase='complete';current_item=None
        progress(message,always=True)
        with db.connect() as conn:
            conn.execute('UPDATE jobs SET status=?,finished_at=?,done=total WHERE id=?',(status,now(),job_id))
        if status=='complete' and entire:
            db.update_settings({'last_sync_date':latest})
    except Exception as error:
        errors.append(str(error)[:250])
        phase='failed';current_item=None
        progress(str(error)[:250],always=True)
        with db.connect() as conn:
            conn.execute('UPDATE jobs SET status=?,finished_at=?,message=?,errors=? WHERE id=?',
                         ('failed',now(),str(error)[:250],json.dumps(errors,ensure_ascii=False),job_id))
    finally:
        _sync_lock.release()


def dashboard(broker='9300', branch='9339', as_of=None):
    ignored=db.ignored_symbols()
    with db.connect() as conn:
        selected = conn.execute('SELECT * FROM branches WHERE broker=? AND branch=?',(broker,branch)).fetchone()
        if not selected:
            raise ValueError('分點尚未加入追蹤')
        snapshots = [dict(r) for r in conn.execute('SELECT * FROM snapshots WHERE broker=? AND branch=? ORDER BY date DESC',(broker,branch))]
        available = [r['date'] for r in snapshots if r['status']=='observed']
        if as_of and as_of not in available:
            raise ValueError('此日期沒有已驗證的分點榜單')
        as_of = as_of or (available[0] if available else None)
        flows = [dict(r) for r in conn.execute('SELECT * FROM flows WHERE broker=? AND branch=? AND date<=? ORDER BY date,symbol',(broker,branch,as_of or '')) if r['symbol'] not in ignored]
        first = flows[0]['date'] if flows else (as_of or today().isoformat())
        prices = [dict(r) for r in conn.execute('SELECT * FROM prices WHERE date>=? AND date<=?',(first,as_of or ''))]
        calendar = [r['date'] for r in conn.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date>=? AND date<=? ORDER BY date",(first,as_of or ''))]
        coverage = [dict(r) for r in conn.execute('SELECT * FROM history_intervals WHERE broker=? AND branch=?',(broker,branch)) if r['symbol'] not in ignored]
        candidate_count = sum(r['symbol'] not in ignored for r in conn.execute('SELECT symbol FROM candidates WHERE broker=? AND branch=?',(broker,branch)))
        failed_market = conn.execute("SELECT COUNT(*) FROM market_days WHERE date>=? AND date<=? AND (twse_status='error' OR tpex_status='error')",(first,as_of or '')).fetchone()[0]
        failed_calendar = conn.execute("SELECT COUNT(*) FROM market_days WHERE date>=? AND date<=? AND twse_status='error'",(first,as_of or '')).fetchone()[0]
        major_balances={}
        for row in conn.execute("SELECT symbol,payload FROM major_turnover WHERE date=? AND status='complete'",(as_of,)):
            cached=json.loads(row['payload']);balance=cached.get('pool_balance')
            if balance:major_balances[row['symbol']]=dict(balance,start=cached['start'],end=cached['end'])
    full = {r['symbol'] for r in coverage if r['start']<=first and r['end']>=(as_of or '')}
    result = analyze(flows,prices,calendar,as_of or today().isoformat(),not failed_calendar,full,coverage)
    for row in result['watchlist']:
        row['history_verified'] = row['symbol'] in full
    result.update(branch=dict(selected),branches=branches(),as_of=as_of,available_dates=available,
        major_balances=major_balances,
        first_date=first if flows else None,settings=db.settings(),job=job_status(),
        quality={'candidate_count':candidate_count,'history_count':len(full),
                 'flow_count':len(flows),'failed_market_days':failed_market,
                 'failed_snapshot_days':sum(r['status']=='error' and r['date']<=(as_of or '') for r in snapshots),
                 'snapshots':snapshots[:60], 'discovery_partial':True})
    return result


def stock_detail(broker, branch, symbol, as_of=None):
    if symbol.upper() in db.ignored_symbols():raise ValueError('此股票已終止交易並設定為忽略')
    report = dashboard(broker,branch,as_of)
    as_of = report['as_of']
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT f.*,p.close,p.open,p.high,p.low FROM flows f LEFT JOIN prices p ON p.symbol=f.symbol AND p.date=f.date WHERE broker=? AND branch=? AND f.symbol=? AND f.date<=? ORDER BY f.date',
                 (broker,branch,symbol,as_of or ''))]
        quotes = [dict(r) for r in conn.execute('SELECT * FROM prices WHERE symbol=? AND date>=? AND date<=? ORDER BY date',(symbol,report['first_date'] or '',as_of or ''))]
    return {'symbol':symbol,'name':rows[-1]['name'] if rows else symbol,'flows':rows,'prices':quotes,
        'position':next((r for r in report['positions'] if r['symbol']==symbol),None),
        'signals':[r for r in report['signals'] if r['symbol']==symbol],
        'cycles':[r for r in report['cycles'] if r['symbol']==symbol],
        'stock_stats':next((r for r in report['stock_stats'] if r['symbol']==symbol),None),
        'source':sources.BASE+'/z/zc/zco/zco0/zco0.djhtm?'+sources.urlencode({'A':symbol,'BHID':broker,'b':branch,'C':'3'})}


def scheduler_tick(current, config):
    # Import locally: full_update already imports service. One shared job lock.
    from . import full_update
    if not config.get('schedule_enabled') or current.weekday()>=5 or current.strftime('%H:%M')<config.get('schedule_time','18:00'):
        return False
    day=current.date().isoformat();slot=current.strftime('%Y-%m-%d %H')
    if config.get('home_schedule_slot')==slot:return False
    job=full_update.status()
    if job.get('status')=='running':return False
    if job.get('status')=='complete' and config.get('home_full_success_date')==day:return False
    full_update.start()
    with db.connect() as c:c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('home_schedule_slot', json.dumps(slot)))
    return True


def scheduler():
    """Local process only. Catch missed dates on launch, retry delayed post-close feeds hourly."""
    while True:
        try:scheduler_tick(datetime.now(TZ), db.settings())
        except Exception:
            # A transient source/storage failure must not kill future attempts.
            import logging
            logging.getLogger(__name__).exception('首頁排程更新失敗，下一輪重試')
        threading.Event().wait(30)
