"""Durable, explicitly started suffix-repair queue for branch-stock history."""
from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from datetime import date,datetime,timedelta

from . import db,service,sources,sync_audit

TIERS=('R0_OPEN','R1_LATEST','R2_RECENT','R3_RECONCILE','R4_ARCHIVE')
STATUSES=('queued','running','complete','failed','interrupted')
MAX_ATTEMPTS=5
MAX_BATCH=50000
BACKOFF_MINUTES=(15,60,240,720,1440)
_active_run_id=None
_active_lock=threading.Lock()


def _tables(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS history_repair_plans (
      id TEXT PRIMARY KEY, created_at TEXT NOT NULL, audit_id TEXT,
      through_date TEXT, target_date TEXT, selected INTEGER NOT NULL,
      summary TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS history_repair_queue (
      broker TEXT NOT NULL, branch TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
      repair_tier TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT NOT NULL DEFAULT '', plan_id TEXT NOT NULL, run_id TEXT,
      queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
      error_kind TEXT NOT NULL DEFAULT '', next_retry_at TEXT,
      retryable INTEGER NOT NULL DEFAULT 1, checkpoint_at TEXT,
      PRIMARY KEY(broker,branch,symbol,start,end)
    );
    CREATE INDEX IF NOT EXISTS idx_history_repair_queue ON history_repair_queue(status,repair_tier,start);
    CREATE TABLE IF NOT EXISTS history_repair_runs (
      id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
      status TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
      succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
      message TEXT NOT NULL DEFAULT '', errors TEXT NOT NULL DEFAULT '[]',
      checkpoint_at TEXT, checkpoint_item TEXT
    );
    ''')
    queue_columns={row['name'] for row in conn.execute('PRAGMA table_info(history_repair_queue)')}
    for name,definition in [('error_kind',"TEXT NOT NULL DEFAULT ''"),('next_retry_at','TEXT'),
                            ('retryable','INTEGER NOT NULL DEFAULT 1'),('checkpoint_at','TEXT')]:
        if name not in queue_columns:conn.execute(f'ALTER TABLE history_repair_queue ADD COLUMN {name} {definition}')
    run_columns={row['name'] for row in conn.execute('PRAGMA table_info(history_repair_runs)')}
    for name in ['checkpoint_at','checkpoint_item']:
        if name not in run_columns:conn.execute(f'ALTER TABLE history_repair_runs ADD COLUMN {name} TEXT')
    with _active_lock:active=_active_run_id
    params=[];extra=''
    if active:
        extra=' AND (run_id IS NULL OR run_id<>?)';params.append(active)
    stale=[r['run_id'] for r in conn.execute("SELECT DISTINCT run_id FROM history_repair_queue WHERE status='running'"+extra,params)]
    if stale:
        stopped=service.now()
        marks=','.join('?' for _ in stale)
        conn.execute("UPDATE history_repair_queue SET status='interrupted',finished_at=?,checkpoint_at=?,last_error='程式中斷，可繼續回補',error_kind='interrupted',next_retry_at=NULL,retryable=1 WHERE status='running' AND run_id IN ("+marks+")",(stopped,stopped,*stale))
        conn.execute("UPDATE history_repair_runs SET status='failed',finished_at=?,message='程式中斷，可從剩餘缺口繼續' WHERE status='running' AND id IN ("+marks+")",(stopped,*stale))


def _day_after(value):return (date.fromisoformat(value)+timedelta(days=1)).isoformat()


def _validate_through(value):
    if value in [None,'']:return None
    if not isinstance(value,str):raise ValueError('停滯日期需為 YYYY-MM-DD')
    try:parsed=date.fromisoformat(value)
    except ValueError:raise ValueError('停滯日期需為有效的 YYYY-MM-DD')
    if parsed.isoformat()!=value:raise ValueError('停滯日期需為有效的 YYYY-MM-DD')
    return value


def _retry_time(attempt,error_kind):
    base=1440 if error_kind=='source_pending' else BACKOFF_MINUTES[min(max(attempt-1,0),len(BACKOFF_MINUTES)-1)]
    return (datetime.now(service.TZ)+timedelta(minutes=base)).isoformat(timespec='seconds')


def _error_kind(error):
    message=str(error).lower()
    if '缺少已觀察榜單交易' in str(error) or '等待來源更新' in str(error):return 'source_pending'
    if isinstance(error,(TimeoutError,ConnectionError)) or any(word in message for word in ['timeout','timed out','connection','temporarily','http 429']):return 'network'
    if isinstance(error,sources.SourceError):return 'source_error'
    return 'unexpected'


def _activate_retries(conn,limit,force=False):
    at=service.now()
    condition="""status='interrupted' OR
      (status='failed' AND retryable=1 AND attempts<? AND (? OR next_retry_at IS NULL OR next_retry_at<=?))"""
    rows=[r['rowid'] for r in conn.execute(f'''SELECT rowid FROM history_repair_queue WHERE {condition}
      ORDER BY CASE status WHEN 'interrupted' THEN 0 ELSE 1 END,attempts,
      CASE repair_tier WHEN 'R0_OPEN' THEN 0 WHEN 'R1_LATEST' THEN 1 WHEN 'R2_RECENT' THEN 2 WHEN 'R3_RECONCILE' THEN 3 ELSE 4 END,
      start,broker,branch,symbol LIMIT ?''',(MAX_ATTEMPTS,int(force),at,limit))]
    if rows:
        marks=','.join('?' for _ in rows)
        conn.execute(f"""UPDATE history_repair_queue SET status='queued',run_id=NULL,started_at=NULL,
            finished_at=NULL,next_retry_at=NULL WHERE rowid IN ({marks})""",rows)
    return len(rows)


def plan(through_date=None,refresh_audit=True):
    """Build queue locally. No source request is made by this operation."""
    through_date=_validate_through(through_date)
    if not isinstance(refresh_audit,bool):raise ValueError('refresh_audit 需為布林值')
    if refresh_audit:sync_audit.run(20)
    created=service.now();plan_id=uuid.uuid4().hex
    with db.connect() as conn:
        _tables(conn)
        audit=conn.execute('SELECT id,as_of,status FROM sync_audit_runs ORDER BY started_at DESC,rowid DESC LIMIT 1').fetchone()
        if not audit or audit['status']!='complete':raise ValueError('請先完成本機同步盤點')
        clauses=["branch_active=1","ignored=0","repair_tier IN ('R0_OPEN','R1_LATEST','R2_RECENT','R3_RECONCILE','R4_ARCHIVE')",
                 'target_date IS NOT NULL','next_query_start IS NOT NULL']
        params=[]
        if through_date:clauses.append("COALESCE(last_success_date,'0000-00-00')<=?");params.append(through_date)
        candidates=[dict(r) for r in conn.execute(
            'SELECT * FROM candidate_sync_state WHERE '+' AND '.join(clauses),params)]
        target=max((r['target_date'] for r in candidates),default=audit['as_of'])
        inserted=0
        for row in candidates:
            start=row['next_query_start'];end=row['target_date']
            existing=[dict(r) for r in conn.execute('''SELECT * FROM history_repair_queue
                WHERE broker=? AND branch=? AND symbol=?
                  AND status IN ('queued','failed','interrupted') AND start<=? AND end>=?''',
                (row['broker'],row['branch'],row['symbol'],end,start))]
            if len(existing)==1 and existing[0]['start']==start and existing[0]['end']==end:
                conn.execute('''UPDATE history_repair_queue SET name=?,repair_tier=?,plan_id=?
                    WHERE broker=? AND branch=? AND symbol=? AND start=? AND end=?''',
                    (row['name'],row['repair_tier'],plan_id,row['broker'],row['branch'],row['symbol'],start,end))
                continue
            if existing:
                start=min([start]+[item['start'] for item in existing]);end=max([end]+[item['end'] for item in existing])
                conn.executemany('''DELETE FROM history_repair_queue
                    WHERE broker=? AND branch=? AND symbol=? AND start=? AND end=?''',
                    [(item['broker'],item['branch'],item['symbol'],item['start'],item['end']) for item in existing])
            conn.execute('''INSERT INTO history_repair_queue
                (broker,branch,symbol,name,repair_tier,start,end,status,attempts,last_error,plan_id,queued_at)
                VALUES (?,?,?,?,?,?,?,'queued',0,'',?,?)''',
                (row['broker'],row['branch'],row['symbol'],row['name'],row['repair_tier'],start,end,plan_id,created))
            inserted+=1
        conn.execute("""UPDATE history_repair_queue SET status='complete',finished_at=?,last_error='',
            error_kind='',next_retry_at=NULL,retryable=0,checkpoint_at=?
            WHERE status IN ('queued','failed','interrupted') AND EXISTS (
              SELECT 1 FROM history_intervals h WHERE h.broker=history_repair_queue.broker
                AND h.branch=history_repair_queue.branch AND h.symbol=history_repair_queue.symbol
                AND h.start<=history_repair_queue.start AND h.end>=history_repair_queue.end)""",(created,created))
        tier_counts=Counter(r['repair_tier'] for r in candidates)
        summary={'selected':len(candidates),'inserted':inserted,'by_tier':{tier:tier_counts.get(tier,0) for tier in TIERS},
                 'network_requests':0,'range':'first_missing_range_to_target'}
        conn.execute('INSERT INTO history_repair_plans VALUES (?,?,?,?,?,?,?)',
            (plan_id,created,audit['id'],through_date,target,len(candidates),json.dumps(summary,ensure_ascii=False)))
    return report(0)


def _latest_run(conn):
    row=conn.execute('SELECT * FROM history_repair_runs ORDER BY started_at DESC,rowid DESC LIMIT 1').fetchone()
    if not row:return None
    value=dict(row);value['errors']=json.loads(value['errors']);return value


def report(limit=100,status=None,repair_tier=None):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 0<=limit<=500:raise ValueError('修復明細筆數需為 0～500')
    if status is not None and status not in STATUSES:raise ValueError('修復狀態無效')
    if repair_tier is not None and repair_tier not in TIERS:raise ValueError('修復優先級無效')
    with db.connect() as conn:
        _tables(conn)
        plan_row=conn.execute('SELECT * FROM history_repair_plans ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
        latest_plan=dict(plan_row) if plan_row else None
        if latest_plan:latest_plan['summary']=json.loads(latest_plan['summary'])
        status_counts={r['status']:r['count'] for r in conn.execute('SELECT status,COUNT(*) count FROM history_repair_queue GROUP BY status')}
        tier_counts={r['repair_tier']:r['count'] for r in conn.execute("SELECT repair_tier,COUNT(*) count FROM history_repair_queue WHERE status='queued' GROUP BY repair_tier")}
        at=service.now()
        retry=dict(conn.execute('''SELECT
          SUM(CASE WHEN status='failed' AND retryable=1 AND attempts<? AND (next_retry_at IS NULL OR next_retry_at<=?) THEN 1 ELSE 0 END) due,
          SUM(CASE WHEN status='failed' AND retryable=1 AND attempts<? AND next_retry_at>? THEN 1 ELSE 0 END) waiting,
          SUM(CASE WHEN status='failed' AND (retryable=0 OR attempts>=?) THEN 1 ELSE 0 END) exhausted,
          SUM(CASE WHEN status='interrupted' THEN 1 ELSE 0 END) interrupted
          FROM history_repair_queue''',(MAX_ATTEMPTS,at,MAX_ATTEMPTS,at,MAX_ATTEMPTS)).fetchone())
        clauses=[];params=[]
        if status:clauses.append('status=?');params.append(status)
        if repair_tier:clauses.append('repair_tier=?');params.append(repair_tier)
        where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
        order=""" ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'failed' THEN 2 WHEN 'interrupted' THEN 3 ELSE 4 END,
            CASE repair_tier WHEN 'R0_OPEN' THEN 0 WHEN 'R1_LATEST' THEN 1 WHEN 'R2_RECENT' THEN 2 WHEN 'R3_RECONCILE' THEN 3 ELSE 4 END,start,broker,branch,symbol"""
        rows=[dict(r) for r in conn.execute('SELECT * FROM history_repair_queue'+where+order+' LIMIT ?',(*params,limit))] if limit else []
        job=_latest_run(conn)
    return {'plan':latest_plan,'job':job,'counts':{s:status_counts.get(s,0) for s in STATUSES},
            'queued_by_tier':{t:tier_counts.get(t,0) for t in TIERS},'rows':rows,
            'retry':{key:int(retry.get(key) or 0) for key in ['due','waiting','exhausted','interrupted']},
            'retry_policy':{'max_attempts':MAX_ATTEMPTS,'backoff_minutes':list(BACKOFF_MINUTES)},
            'statuses':list(STATUSES),'repair_tiers':list(TIERS)}


def requeue(limit=500,force=False):
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=MAX_BATCH:raise ValueError(f'重新排隊筆數需為 1～{MAX_BATCH}')
    if not isinstance(force,bool):raise ValueError('force 需為布林值')
    if not service._sync_lock.acquire(blocking=False):raise ValueError('每日同步或歷史修復正在執行')
    try:
        with db.connect() as conn:
            _tables(conn);activated=_activate_retries(conn,limit,force)
    finally:service._sync_lock.release()
    result=report(0);result['activated']=activated;return result


def start(limit=50,resume=True):
    global _active_run_id
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=MAX_BATCH:raise ValueError(f'單批修復筆數需為 1～{MAX_BATCH}')
    if not isinstance(resume,bool):raise ValueError('resume 需為布林值')
    if not service._sync_lock.acquire(blocking=False):raise ValueError('每日同步或另一批歷史修復正在執行')
    worker_started=False
    try:
        run_id=uuid.uuid4().hex;started=service.now()
        with db.connect() as conn:
            _tables(conn)
            if resume:_activate_retries(conn,limit,False)
            selected=[dict(r) for r in conn.execute("""SELECT * FROM history_repair_queue WHERE status='queued'
                ORDER BY CASE repair_tier WHEN 'R0_OPEN' THEN 0 WHEN 'R1_LATEST' THEN 1 WHEN 'R2_RECENT' THEN 2 WHEN 'R3_RECONCILE' THEN 3 ELSE 4 END,
                attempts,start,broker,branch,symbol LIMIT ?""",(limit,))]
            if not selected:
                service._sync_lock.release()
                return report(0)
            conn.execute('''INSERT INTO history_repair_runs
                (id,started_at,finished_at,status,done,total,succeeded,failed,message,errors,checkpoint_at,checkpoint_item)
                VALUES (?,?,NULL,?,0,?,0,0,?,?,NULL,NULL)''',
                (run_id,started,'running',len(selected),f'歷史尾端修復 0/{len(selected)}','[]'))
            conn.executemany("""UPDATE history_repair_queue SET status='running',run_id=?,attempts=attempts+1,
                started_at=?,finished_at=NULL,last_error='' WHERE broker=? AND branch=? AND symbol=? AND start=? AND end=?""",
                [(run_id,started,r['broker'],r['branch'],r['symbol'],r['start'],r['end']) for r in selected])
        with _active_lock:_active_run_id=run_id
        threading.Thread(target=_worker,args=(run_id,selected),daemon=True).start()
        worker_started=True
        return report(0)
    except Exception:
        with _active_lock:
            if _active_run_id==locals().get('run_id'):_active_run_id=None
        if not worker_started and service._sync_lock.locked():service._sync_lock.release()
        raise


def _worker(run_id,selected):
    global _active_run_id
    errors=[];succeeded=failed=0
    try:
        for index,item in enumerate(selected,1):
            attempt=item['attempts']+1
            try:
                rows=sources.history(item['broker'],item['branch'],item['symbol'],item['name'],item['start'],item['end'],refresh=True)
                service._store_history(item['broker'],item['branch'],item['symbol'],item['start'],item['end'],rows)
                state='complete';error='';kind='';retryable=0;next_retry=None;succeeded+=1
            except Exception as exc:
                state='failed';error=str(exc)[:220];kind=_error_kind(exc);retryable=int(attempt<MAX_ATTEMPTS)
                next_retry=_retry_time(attempt,kind) if retryable else None;failed+=1
                errors.append(f"{item['name']} {item['symbol']} {item['start']}～{item['end']}：{error}")
            finished=service.now()
            checkpoint=json.dumps({'broker':item['broker'],'branch':item['branch'],'symbol':item['symbol'],
                'start':item['start'],'end':item['end'],'status':state,'attempt':attempt},ensure_ascii=False)
            with db.connect() as conn:
                conn.execute('''UPDATE history_repair_queue SET status=?,finished_at=?,checkpoint_at=?,last_error=?,
                    error_kind=?,next_retry_at=?,retryable=?
                    WHERE broker=? AND branch=? AND symbol=? AND start=? AND end=?''',
                    (state,finished,finished,error,kind,next_retry,retryable,item['broker'],item['branch'],item['symbol'],item['start'],item['end']))
                conn.execute('''UPDATE history_repair_runs SET done=?,succeeded=?,failed=?,message=?,errors=?,
                    checkpoint_at=?,checkpoint_item=? WHERE id=?''',
                    (index,succeeded,failed,f'歷史尾端修復 {index}/{len(selected)}',json.dumps(errors,ensure_ascii=False),finished,checkpoint,run_id))
        status='partial' if failed else 'complete';message=f'本批完成 {succeeded} 組'
        if failed:message+=f'，{failed} 組失敗；下次批次將依退避時間續排'
        with db.connect() as conn:
            conn.execute('UPDATE history_repair_runs SET status=?,finished_at=?,message=?,errors=? WHERE id=?',
                (status,service.now(),message,json.dumps(errors,ensure_ascii=False),run_id))
        try:sync_audit.run(20)
        except Exception:pass
    except Exception as exc:
        with db.connect() as conn:
            stopped=service.now()
            conn.execute("""UPDATE history_repair_queue SET status='interrupted',finished_at=?,checkpoint_at=?,
                last_error=?,error_kind='interrupted',next_retry_at=NULL,retryable=1
                WHERE run_id=? AND status='running'""",(stopped,stopped,str(exc)[:220],run_id))
            conn.execute('UPDATE history_repair_runs SET status=?,finished_at=?,message=?,errors=? WHERE id=?',
                ('failed',service.now(),str(exc)[:220],json.dumps(errors+[str(exc)[:220]],ensure_ascii=False),run_id))
    finally:
        with _active_lock:
            if _active_run_id==run_id:_active_run_id=None
        service._sync_lock.release()
