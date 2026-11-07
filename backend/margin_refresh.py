"""Daily observational refresh. Frozen research and manual accounts stay untouched."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import json, sqlite3, threading
from . import db, margin_statistics as src

_lock=threading.Lock()
_state_lock=threading.Lock()
_job={'status':'idle','message':'尚未執行每日融資更新'}

def status():
 with _state_lock:return dict(_job)

def monthly_date():
 from . import monthly_statistics
 try:return max(monthly_statistics._read()['markets']['taiex']['closes'],key=lambda r:r['date'])['date']
 except (OSError,ValueError,KeyError):return None

def latest_date():
 """The date all five sources and both markets' observation rows actually cover."""
 p=src.folder()/'observations.sqlite3'
 if not p.exists() or not (src.folder()/'sources.sqlite3').exists():return None
 try:
  with src.connect() as c:
   row=c.execute("SELECT MAX(day) FROM (SELECT day FROM latest GROUP BY day HAVING COUNT(*)=5)").fetchone();official=row[0]
  with sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True) as c:
   observed=c.execute('SELECT MAX(day) FROM (SELECT day FROM days WHERE available=1 GROUP BY day HAVING COUNT(*)=2)').fetchone()[0]
  return min(official,observed) if official and observed else None
 except sqlite3.Error:return None

@contextmanager
def live_input():
 """Same estimator input schema, joined from versioned official snapshots.
 No cold frozen research is hydrated or mutated, and missing days stay gaps.
 """
 with src.connect() as c:
  c.executescript('''
  CREATE TEMP VIEW inputs AS
  SELECT l.market,l.day,c.symbol,c.name,c.previous,c.buy,c.sell,c.repay,c.balance,c.notes,
         q.close,q.open,q.high,q.low,q.volume,q.turnover,
         CASE WHEN l.market='TWSE' THEN s.shares ELSE q.shares END AS shares
  FROM latest l JOIN credit c ON c.snapshot_id=l.snapshot_id
  JOIN latest ql ON ql.market=l.market AND ql.day=l.day AND ql.kind='quotes'
  LEFT JOIN quotes q ON q.snapshot_id=ql.snapshot_id AND q.symbol=c.symbol
  LEFT JOIN latest sl ON sl.market=l.market AND sl.day=l.day AND sl.kind='shares'
  LEFT JOIN shares s ON s.snapshot_id=sl.snapshot_id AND s.symbol=c.symbol
  WHERE l.kind='credit' AND l.day>='2023-11-01';
  CREATE TEMP VIEW totals AS
  SELECT l.market,l.day,t.loan_amount FROM latest l JOIN main.totals t ON t.snapshot_id=l.snapshot_id WHERE l.kind='credit' AND l.day>='2023-11-01';
  CREATE TEMP VIEW calendar AS SELECT * FROM main.calendar WHERE day>='2023-11-01';
  CREATE TEMP VIEW events AS SELECT * FROM extras WHERE kind='corporate_events';
  ''')
  c.execute('BEGIN')
  yield c

def refresh_sync():
 from . import margin_statistics_sources as collector, margin_observation_build as builder
 now=datetime.now(src.TZ)
 # Price/market calendars supply a real trading date, not today's weekday guess.
 with db.connect() as c:
  row=c.execute("SELECT MAX(date) FROM market_days WHERE date<=? AND (twse_status='ok' OR tpex_status='ok')",(now.date().isoformat(),)).fetchone()
  target=row[0]
  if not target:target=c.execute('SELECT MAX(date) FROM prices WHERE date<=?',(now.date().isoformat(),)).fetchone()[0]
 if not target:raise ValueError('尚無已完成台股交易日，請先更新共用行情')
 if target==now.date().isoformat() and now.strftime('%H:%M')<'14:00':
  raise ValueError('今日尚未收盤，保留上一交易日融資資料')
 src.initialize()
 with src.connect() as c:
  last=c.execute('SELECT MAX(day) FROM latest').fetchone()[0]
  before=[tuple(r) for r in c.execute('SELECT market,kind,day,snapshot_id FROM latest ORDER BY market,kind,day')]
  event_before=[r[0] for r in c.execute("SELECT id FROM extras WHERE kind='corporate_events' ORDER BY id")]
 # Initial three-year backfill is finite; subsequent daily refreshes only fill
 # the new tail and recent gaps. A no-change refresh never rebuilds the model.
 start=max(src.START,(datetime.fromisoformat(last)-timedelta(days=10)).date().isoformat()) if last else src.START
 collected=collector.run(start=start,end=target,workers=2,post_close=True,retry=True)
 with src.connect() as c:
  after=[tuple(r) for r in c.execute('SELECT market,kind,day,snapshot_id FROM latest ORDER BY market,kind,day')]
  event_after=[r[0] for r in c.execute("SELECT id FROM extras WHERE kind='corporate_events' ORDER BY id")]
 if before!=after or event_before!=event_after or latest_date()!=target:
  with _state_lock:_job.update(message='官方來源已保存，重算兩種成本估算與RS子集')
  builder.build(live=True)
 else:
  # RS can advance even when official financing snapshots do not change.
  p=src.folder()/'observations.sqlite3'
  import gzip
  with sqlite3.connect(p) as out, db.connect() as monitor:
   end=out.execute('SELECT MAX(day) FROM calendar').fetchone()[0]
   for row in monitor.execute('SELECT date,payload,fetched_at FROM rs_days WHERE date<=?',(end,)):
    if json.loads(row['payload']).get('as_of')==row['date']:
     out.execute('INSERT OR REPLACE INTO rs VALUES(?,?,?)',(row['date'],gzip.compress(row['payload'].encode(),mtime=0),row['fetched_at']))
 latest=latest_date();complete=latest==target and collected['status']=='complete'
 return {'status':'complete' if complete else 'partial', 'as_of':latest,
         'message':f'融資統計已核對至 {latest or "尚無完整日期"}；'+('上市／上櫃、ETF、分布與子集已更新' if complete else ('歷史來源仍有缺漏，請查看品質清單' if latest==target else f'目標 {target}，部分官方來源尚未發布或缺漏，保留原值')),
         'target':target,'source_status':collected['status']}

def refresh():
 if not _lock.acquire(blocking=False):return status()
 with _state_lock:_job.update(status='running',message='更新官方融資、行情與發行股數',started_at=src.now())
 def worker():
  try:result=refresh_sync()
  except Exception as e:result={'status':'failed','message':'融資更新未完成，保留既有資料：'+str(e)[:180]}
  finally:
   with _state_lock:_job.update(result,finished_at=src.now())
   _lock.release()
 try:threading.Thread(target=worker,daemon=True).start()
 except Exception:
  with _state_lock:_job.update(status='failed',message='無法啟動融資更新')
  _lock.release();raise
 return status()
