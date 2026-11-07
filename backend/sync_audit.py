"""Local-only inventory for deciding daily and historical branch-stock work."""
from __future__ import annotations

import json
import math
import uuid
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .coverage import missing_ranges

TZ=ZoneInfo('Asia/Taipei')


def now():return datetime.now(TZ).isoformat(timespec='seconds')

TIERS=('P0_OPEN','P1_LATEST','P2_RECENT','P3_RECONCILE','SLEEP','IGNORED','INACTIVE')
REPAIR_TIERS=('R0_OPEN','R1_LATEST','R2_RECENT','R3_RECONCILE','R4_ARCHIVE','DONE','IGNORED','INACTIVE')


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS sync_audit_runs (
      id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
      status TEXT NOT NULL, as_of TEXT, recent_cutoff TEXT,
      total INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '{}',
      error TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS candidate_sync_state (
      broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
      branch_active INTEGER NOT NULL, daily_tier TEXT NOT NULL, repair_tier TEXT NOT NULL,
      estimated_position REAL NOT NULL, unmatched_outflow REAL NOT NULL,
      first_flow_date TEXT, last_flow_date TEXT, last_ranking_date TEXT,
      last_success_date TEXT, target_date TEXT, next_query_start TEXT,
      recent_cutoff TEXT, coverage_complete INTEGER NOT NULL,
      ignored INTEGER NOT NULL, reason TEXT NOT NULL, audited_at TEXT NOT NULL,
      PRIMARY KEY(broker,branch,symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_candidate_sync_daily ON candidate_sync_state(branch_active,daily_tier);
    CREATE INDEX IF NOT EXISTS idx_candidate_sync_repair ON candidate_sync_state(repair_tier,next_query_start);
    ''')


def _priority(active,ignored,position,latest_ranking,recent,unmatched,has_gap):
    if not active:return 'INACTIVE','INACTIVE','分點未列入目前追蹤名單'
    if ignored:return 'IGNORED','IGNORED','股票已由使用者標記為終止交易或忽略'
    if position>.000001:return 'P0_OPEN','R0_OPEN' if has_gap else 'DONE','仍有推估部位，列為每日最高優先'
    if latest_ranking:return 'P1_LATEST','R1_LATEST' if has_gap else 'DONE','有保存到最新榜單來源證據'
    if recent:return 'P2_RECENT','R2_RECENT' if has_gap else 'DONE','最近20個交易日仍有分點交易'
    if unmatched>.000001 and has_gap:return 'P3_RECONCILE','R3_RECONCILE','曾有期初未知流出且歷史仍有缺口'
    return 'SLEEP','R4_ARCHIVE' if has_gap else 'DONE','沒有推估部位且近期未交易；保留歷史並停止每日查詢'


def run(recent_sessions=20):
    """Audit current SQLite data only. This function performs no HTTP requests."""
    if not isinstance(recent_sessions,int) or isinstance(recent_sessions,bool) or not 5<=recent_sessions<=60:
        raise ValueError('近期活躍交易日需為5～60日')
    run_id=uuid.uuid4().hex;started=now()
    with db.connect() as conn:
        _tables(conn)
        conn.execute('INSERT INTO sync_audit_runs(id,started_at,status) VALUES (?,?,?)',(run_id,started,'running'))
    try:
        with db.connect() as conn:
            _tables(conn)
            branch_rows={
                (r['broker'],r['branch']):dict(r) for r in conn.execute('SELECT * FROM branches')
            }
            targets={
                (r['broker'],r['branch']):r['target'] for r in conn.execute(
                    "SELECT broker,branch,MAX(date) target FROM snapshots WHERE status='observed' GROUP BY broker,branch")
            }
            first_dates={
                (r['broker'],r['branch']):r['first'] for r in conn.execute(
                    "SELECT broker,branch,MIN(date) first FROM snapshots WHERE status='observed' GROUP BY broker,branch")
            }
            candidates={
                (r['broker'],r['branch'],r['symbol']):dict(r) for r in conn.execute('SELECT * FROM candidates')
            }
            settings={r['key']:json.loads(r['value']) for r in conn.execute('SELECT * FROM settings')}
            ignored={str(x).strip().upper() for x in settings.get('ignored_symbols',[]) if str(x).strip()}
            calendars=[r['date'] for r in conn.execute("SELECT date FROM market_days WHERE twse_status='ok' ORDER BY date")]
            cutoffs={}
            for target in set(targets.values()):
                eligible=[day for day in calendars if day<=target]
                cutoffs[target]=eligible[-recent_sessions] if len(eligible)>=recent_sessions else (eligible[0] if eligible else target)
            intervals=defaultdict(list)
            for row in conn.execute('SELECT broker,branch,symbol,start,end FROM history_intervals ORDER BY broker,branch,symbol,start'):
                intervals[(row['broker'],row['branch'],row['symbol'])].append({'start':row['start'],'end':row['end']})
            activity={key:{'position':0.0,'unmatched':0.0,'first':None,'last':None,'last_ranking':None} for key in candidates}
            for row in conn.execute('SELECT broker,branch,symbol,date,net,source FROM flows ORDER BY broker,branch,symbol,date'):
                key=(row['broker'],row['branch'],row['symbol'])
                if key not in activity:continue
                item=activity[key];value=float(row['net']) if _finite(row['net']) else 0.0
                item['first']=item['first'] or row['date'];item['last']=row['date']
                if 'zgb0.djhtm' in row['source']:item['last_ranking']=row['date']
                if value>=0:item['position']+=value
                else:
                    sold=-value;matched=min(item['position'],sold);item['position']-=matched;item['unmatched']+=sold-matched
            for row in conn.execute('SELECT broker,branch,symbol,MAX(date) last_ranking FROM ranking_entries GROUP BY broker,branch,symbol'):
                key=(row['broker'],row['branch'],row['symbol'])
                if key in activity and (not activity[key]['last_ranking'] or row['last_ranking']>activity[key]['last_ranking']):activity[key]['last_ranking']=row['last_ranking']
            audited=now();rows=[]
            for key,candidate in candidates.items():
                broker,branch,symbol=key;branch_row=branch_rows.get((broker,branch),{});active=bool(branch_row.get('active'))
                target=targets.get((broker,branch));first=first_dates.get((broker,branch));cutoff=cutoffs.get(target,target)
                spans=intervals.get(key,[]);last_success=max((r['end'] for r in spans),default=None)
                gaps=missing_ranges(first,target,spans) if first and target else []
                next_query=gaps[0][0] if gaps else None;state=activity[key]
                latest_ranking=bool(target and state['last_ranking']==target)
                recent=bool(cutoff and state['last'] and state['last']>=cutoff)
                daily,repair,reason=_priority(active,symbol.upper() in ignored,state['position'],latest_ranking,recent,state['unmatched'],bool(gaps))
                rows.append((broker,branch,symbol,candidate['name'],int(active),daily,repair,round(state['position'],4),round(state['unmatched'],4),state['first'],state['last'],state['last_ranking'],last_success,target,next_query,cutoff,int(not gaps),int(symbol.upper() in ignored),reason,audited))
            daily_counts=Counter(r[5] for r in rows);repair_counts=Counter(r[6] for r in rows);end_counts=Counter(r[12] or 'NONE' for r in rows)
            active_rows=[r for r in rows if r[4]]
            summary={
                'total':len(rows),'active_total':len(active_rows),'active_branches':sum(bool(r.get('active')) for r in branch_rows.values()),
                'as_of':max(targets.values(),default=None),'recent_sessions':recent_sessions,
                'recent_cutoff':min((r[15] for r in active_rows if r[15]),default=None),
                'daily_tiers':{tier:daily_counts.get(tier,0) for tier in TIERS},
                'repair_tiers':{tier:repair_counts.get(tier,0) for tier in REPAIR_TIERS},
                'last_success_dates':dict(sorted(end_counts.items())),
                'coverage_complete':sum(r[16] for r in active_rows),
                'coverage_gap':sum(not r[16] for r in active_rows),
                'latest_ranking_confirmed':sum(bool(r[13] and activity[(r[0],r[1],r[2])]['last_ranking']==r[13]) for r in active_rows),
                'latest_ranking_only':sum(r[5]=='P1_LATEST' for r in active_rows),
                'provenance_note':'第二包起每日榜單成員獨立保存；更早資料若已被個股明細取代，仍只能使用殘留來源證據。',
                'network_requests':0,
            }
            conn.execute('DELETE FROM candidate_sync_state')
            conn.executemany('INSERT INTO candidate_sync_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
            conn.execute('UPDATE sync_audit_runs SET finished_at=?,status=?,as_of=?,recent_cutoff=?,total=?,summary=? WHERE id=?',(audited,'complete',summary['as_of'],summary['recent_cutoff'],len(rows),json.dumps(summary,ensure_ascii=False),run_id))
        return report()
    except Exception as error:
        with db.connect() as conn:
            _tables(conn);conn.execute('UPDATE sync_audit_runs SET finished_at=?,status=?,error=? WHERE id=?',(now(),'failed',str(error)[:300],run_id))
        raise


def report(limit=100,daily_tier=None,repair_tier=None):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 0<=limit<=500:raise ValueError('盤點明細筆數需為0～500')
    if daily_tier is not None and daily_tier not in TIERS:raise ValueError('每日優先級無效')
    if repair_tier is not None and repair_tier not in REPAIR_TIERS:raise ValueError('修復優先級無效')
    with db.connect() as conn:
        _tables(conn);run_row=conn.execute('SELECT * FROM sync_audit_runs ORDER BY started_at DESC,rowid DESC LIMIT 1').fetchone()
        run_data=dict(run_row) if run_row else None
        if run_data:
            run_data['summary']=json.loads(run_data['summary'])
        clauses=[];params=[]
        if daily_tier:clauses.append('daily_tier=?');params.append(daily_tier)
        if repair_tier:clauses.append('repair_tier=?');params.append(repair_tier)
        where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
        order=" ORDER BY CASE daily_tier WHEN 'P0_OPEN' THEN 0 WHEN 'P1_LATEST' THEN 1 WHEN 'P2_RECENT' THEN 2 WHEN 'P3_RECONCILE' THEN 3 WHEN 'SLEEP' THEN 4 WHEN 'IGNORED' THEN 5 ELSE 6 END,next_query_start,broker,branch,symbol"
        rows=[dict(r) for r in conn.execute('SELECT * FROM candidate_sync_state'+where+order+' LIMIT ?',(*params,limit))] if limit else []
    return {'run':run_data,'rows':rows,'daily_tiers':list(TIERS),'repair_tiers':list(REPAIR_TIERS)}


def daily_scope(conn,broker,branch,target,recent_sessions=20):
    """Select only current ranking names and previously verified live/recent states."""
    calendar=[r['date'] for r in conn.execute("SELECT date FROM market_days WHERE twse_status='ok' AND date<=? ORDER BY date",(target,))]
    cutoff=calendar[-recent_sessions] if len(calendar)>=recent_sessions else (calendar[0] if calendar else target)
    selected={}
    for row in conn.execute('SELECT DISTINCT symbol,name FROM ranking_entries WHERE broker=? AND branch=? AND date=?',(broker,branch,target)):
        selected[row['symbol']]={'symbol':row['symbol'],'name':row['name'],'reason':'latest_ranking'}
    for row in conn.execute("""SELECT s.symbol,c.name,s.daily_tier FROM candidate_sync_state s
        JOIN candidates c USING(broker,branch,symbol)
        WHERE s.broker=? AND s.branch=? AND s.branch_active=1 AND s.coverage_complete=1
          AND s.daily_tier IN ('P0_OPEN','P1_LATEST','P2_RECENT')""",(broker,branch)):
        selected.setdefault(row['symbol'],{'symbol':row['symbol'],'name':row['name'],'reason':row['daily_tier'].lower()})
    return {'cutoff':cutoff,'rows':list(selected.values())}
