"""Explicit observational build; retains the original trading qualification gates."""
from pathlib import Path
from contextlib import closing
from itertools import groupby
import gzip,json,sqlite3,hashlib,os
from . import db,margin_statistics as src,margin_observation_engine as e,margin_principal_repair as strict,margin_analytics as labels
OUT=db.ROOT/'research/margin_observations_20261105'
INPUT=db.ROOT/'research/margin_statistics_p2_repair_20261105/inputs.sqlite3'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def ro(p):
 c=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c
def stream(c,market,calendar):
 totals={r['day']:r['loan_amount'] for r in c.execute('SELECT * FROM totals WHERE market=?',(market,))};groups=iter(groupby(c.execute('SELECT * FROM inputs WHERE market=? ORDER BY day,symbol',(market,)),lambda r:r['day']));pending=next(groups,None)
 for index,day in enumerate(calendar):
  if pending and pending[0]==day:rows=[dict(r) for r in pending[1]];pending=next(groups,None)
  else:rows=None
  yield index,day,rows,totals.get(day)
def build():
 manifest=json.loads((INPUT.parent/'manifest.json').read_text());assert sha(INPUT)==manifest['inputs_sha256'];assert sha(Path(strict.__file__))==manifest['engine_sha256']
 final=src.folder()/'observations.sqlite3';temp=final.with_suffix('.building.sqlite3')
 if temp.exists():raise ValueError('已有未完成觀察建置，請核對暫存檔')
 c=sqlite3.connect(temp)
 try:
  c.executescript('''CREATE TABLE calendar(day TEXT PRIMARY KEY);CREATE TABLE observations(day TEXT,market TEXT,symbol TEXT,name TEXT,category TEXT,quantity REAL,close REAL,shares REAL,loan_a REAL,loan_b REAL,unknown_a REAL,unknown_b REAL,cap_a REAL,cap_b REAL,unpriced_a REAL,unpriced_b REAL,filled_a INT,filled_b INT,PRIMARY KEY(day,market,symbol));CREATE INDEX observation_market_day ON observations(market,day);CREATE TABLE days(day TEXT,market TEXT,official REAL,available INT,PRIMARY KEY(day,market));CREATE TABLE rs(day TEXT PRIMARY KEY,payload BLOB,saved_at TEXT);CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);''')
  with closing(ro(INPUT)) as frozen:
   calendar=[r[0] for r in frozen.execute('SELECT day FROM calendar ORDER BY day')];c.executemany('INSERT INTO calendar VALUES(?)',[(d,) for d in calendar]);periods={}
   for r in frozen.execute('SELECT * FROM events ORDER BY retrieved_at'):periods[(r['market'],r['period'])]=json.loads(r['payload'])
   counts={}
   for market in ('TWSE','TPEX'):
    events={(r['date'],r['symbol']) for (m,_),v in periods.items() if m==market for r in v['data']['events'] if r['unsafe']};count=0
    # The estimator is unchanged. Only its observational display permission differs.
    for row in e.reconstruct(stream(frozen,market,calendar),events,methods=('net_average','net_fifo'),market=market):
     day=row['date'];c.execute('INSERT INTO days VALUES(?,?,?,?)',(day,market,row['official'],not row['source_missing']))
     if row['source_missing']:continue
     source={r['symbol']:r['shares'] for r in frozen.execute('SELECT symbol,shares FROM inputs WHERE market=? AND day=?',(market,day))}
     batch=[]
     for r in row['securities']:
      a,b=[r['models'][k] for k in ('net_average','net_fifo')]
      batch.append((day,market,r['symbol'],r['name'],labels.classify(r['symbol'],r['name'],market),r['quantity'],r['close'],source[r['symbol']],a['principal'],b['principal'],a['unknown_fraction'],b['unknown_fraction'],a['cap_unknown_units'],b['cap_unknown_units'],a['unpriced_units'],b['unpriced_units'],a['cost_filled'],b['cost_filled']))
     c.executemany('INSERT INTO observations VALUES('+','.join('?'*18)+')',batch);count+=len(batch)
    counts[market]=count;c.commit();print(market,count,flush=True)
  with closing(ro(db.DATA/'monitor.sqlite3')) as monitor:
   for r in monitor.execute('SELECT date,payload,fetched_at FROM rs_days WHERE date BETWEEN ? AND ? ORDER BY date',(calendar[0],calendar[-1])):
    v=json.loads(r['payload'])
    if v.get('as_of')!=r['date']:raise ValueError('RS保存日期不一致')
    c.execute('INSERT INTO rs VALUES(?,?,?)',(r['date'],gzip.compress(r['payload'].encode(),mtime=0),r['fetched_at']))
  meta={'version':'margin-observations-1','created_at':src.now(),'as_of':calendar[-1],'start':calendar[0],'input_sha256':sha(INPUT),'engine_sha256':sha(Path(e.__file__)),'policy_sha256':sha(OUT/'observational-display-policy.json'),'cost_fill_policy_sha256':sha(OUT/'cost-fill-policy.json'),'cost_version':e.VERSION,'model_ready':False,'scope':'observations_not_trading_signals','methods':['net_average','net_fifo'],'counts':counts}
  c.execute('INSERT INTO meta VALUES(?,?)',('manifest',json.dumps(meta,ensure_ascii=False)));c.commit()
 finally:c.close()
 # Preserve the exact source-estimator readout for reproducible observation reports.
 frozen_output=OUT/'observations.sqlite3'
 if frozen_output.exists():raise ValueError('本輪觀察已封存，不覆寫')
 temp.replace(frozen_output)
 import shutil
 candidate=final.with_suffix('.publishing.sqlite3');shutil.copy2(frozen_output,candidate);candidate.replace(final)
 src.atomic(OUT/'manifest.json',{**meta,'database_sha256':sha(frozen_output)});print(json.dumps(meta,ensure_ascii=False))
if __name__=='__main__':build()
