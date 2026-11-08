"""Forward-PE packages 1/2: immutable source, quote and EPS observations only.
No valuation, UI, strategy publication, or implicit network request on read.
"""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,math,re,sqlite3,uuid
from contextlib import contextmanager
from datetime import datetime,date
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db
TZ=ZoneInfo('Asia/Taipei');VERSION='forward-pe-data.v1'
def now():return datetime.now(TZ).isoformat(timespec='microseconds')
def home():return db.DATA/'forward_pe'
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def instant(v):
 t=datetime.fromisoformat(str(v).replace('Z','+00:00'))
 if t.tzinfo is None:raise ValueError('時間必須附時區')
 return t.astimezone(TZ)
def finite(v,positive=False):
 if isinstance(v,bool):raise ValueError('布林值不是數值')
 x=float(v)
 if not math.isfinite(x) or positive and x<=0:raise ValueError('數值無效')
 return x
@contextmanager
def connect(write=False):
 p=home()/'observations.sqlite3'
 if not write and not p.exists():raise FileNotFoundError('尚無估值資料快照')
 if write:home().mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(p if write else p.resolve().as_uri()+'?mode=ro',uri=not write,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
 try:
  yield c
  if write:c.commit()
 except Exception:c.rollback();raise
 finally:c.close()
def initialize():
 with connect(True) as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT NOT NULL,provider TEXT NOT NULL,url TEXT NOT NULL,published_at TEXT,retrieved_at TEXT NOT NULL,ingested_at TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,meta TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS universes(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),effective_date TEXT NOT NULL,known_at TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),symbol TEXT NOT NULL,market_date TEXT NOT NULL,known_at TEXT NOT NULL,payload TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS estimates(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),symbol TEXT NOT NULL,period_kind TEXT NOT NULL,period TEXT NOT NULL,statistic TEXT NOT NULL,known_at TEXT NOT NULL,published_at TEXT NOT NULL,payload TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS eps_lookup ON estimates(symbol,period_kind,period,statistic,known_at);
  CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,version TEXT NOT NULL,payload TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload TEXT NOT NULL);
  ''')
  for table in ('sources','universes','quotes','estimates','snapshots','audits'):
   for operation in ('UPDATE','DELETE'):
    c.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable observation'); END")
def source(kind,provider,url,raw,*,retrieved_at=None,published_at=None,status='ok',meta=None):
 ingested_at=now();stamp=retrieved_at or ingested_at;t=instant(stamp)
 if t>instant(now()):raise ValueError('取得時間不可在未來')
 if published_at and instant(published_at)>t:raise ValueError('發布時間晚於取得時間')
 if not url.startswith(('https://','local://')):raise ValueError('來源須為HTTPS或本機證據')
 if not isinstance(raw,bytes):raise ValueError('原始證據須為bytes')
 sha=hashlib.sha256(raw).hexdigest();root=home()/'raw';root.mkdir(parents=True,exist_ok=True);p=root/(sha+'.gz')
 if not p.exists():
  with p.open('xb') as f:f.write(gzip.compress(raw,mtime=0))
 elif hashlib.sha256(gzip.decompress(p.read_bytes())).hexdigest()!=sha:raise ValueError('原檔雜湊不符，停止保存')
 info={'id':digest([kind,provider,url,sha,stamp]),'kind':kind,'provider':provider,'url':url,'published_at':published_at,'retrieved_at':stamp,'ingested_at':ingested_at,'sha256':sha,'status':status,'meta':meta or {}}
 with connect(True) as c:c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)',(info['id'],kind,provider,url,published_at,stamp,ingested_at,sha,status,canonical(info['meta'])))
 return info['id']
def get_source(c,id):
 row=c.execute('SELECT * FROM sources WHERE id=?',(id,)).fetchone()
 if row is None or row['status']!='ok':raise ValueError('來源缺漏或不合格')
 return dict(row)
def universe(source_id,effective_date,rows,kind='official_month_end'):
 if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',effective_date):raise ValueError('權重日期無效')
 symbols=[r['symbol'] for r in rows];ranks=[r['rank'] for r in rows]
 if len(rows)<50 or len(set(symbols))!=len(rows) or sorted(ranks)!=list(range(1,len(rows)+1)):raise ValueError('成分股代碼或排名重複／不連續')
 for r in rows:
  if not re.fullmatch(r'\d{4}',r['symbol']) or not 0<finite(r['weight_pct'])<=100:raise ValueError('成分股或權重無效')
 total=sum(r['weight_pct'] for r in rows)
 if abs(total-100)>.15:raise ValueError('全體成分股權重加總不符100%')
 with connect(True) as c:
  src=get_source(c,source_id)
  if effective_date>instant(src['retrieved_at']).date().isoformat():raise ValueError('權重日期在未來')
  id=digest([source_id,effective_date,kind,rows]);c.execute('INSERT OR IGNORE INTO universes VALUES(?,?,?,?,?,?)',(id,source_id,effective_date,src['ingested_at'],kind,canonical(rows)))
 return id
def quote_batch(source_id,rows):
 prepared=[]
 with connect(True) as c:
  src=get_source(c,source_id)
  for r in rows:
   date.fromisoformat(r['market_date'])
   if not re.fullmatch(r'\d{4}',r['symbol']) or r['market_date']>instant(src['retrieved_at']).date().isoformat():raise ValueError('股價代碼或日期錯誤')
   finite(r['close'],True)
   if r.get('shares') is not None:finite(r['shares'],True)
   if r.get('currency')!='TWD' or r.get('price_basis')!='raw_close':raise ValueError('股價須為TWD未還原收盤')
   id=digest([source_id,r]);prepared.append((id,source_id,r['symbol'],r['market_date'],src['ingested_at'],canonical(r)))
  c.executemany('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?,?)',prepared)
 return [r[0] for r in prepared]
def validate_estimate(r):
 required=['symbol','period_kind','period','statistic','value','currency','eps_basis','share_basis','forecast_as_of','published_at','provider','estimate_type']
 if any(k not in r for k in required):raise ValueError('預估EPS欄位不足')
 if not re.fullmatch(r'\d{4}',r['symbol']):raise ValueError('股票代碼無效')
 if r['estimate_type']!='analyst_consensus':raise ValueError('僅保存分析師共識；財報或目標價不是預估EPS')
 if r['period_kind'] not in ('calendar_year','fiscal_year','NTM'):raise ValueError('預估期間未明')
 if r['period_kind']=='calendar_year' and not re.fullmatch(r'20\d{2}',r['period']):raise ValueError('預估年度無效')
 if r['period_kind']=='fiscal_year' and not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',r['period']):raise ValueError('會計年度須有期末日期')
 if r['period_kind']=='NTM':
  if not r.get('period_start') or not r.get('period_end') or r['period_end']<=r['period_start']:raise ValueError('NTM須明確起訖，不以今年明年線性混成')
 if r['statistic'] not in ('mean','median'):raise ValueError('不將高低預估或目標價當作共識')
 value=finite(r['value'])
 if r['currency']!='TWD':raise ValueError('目前只收TWD EPS，不隱含換匯')
 for k in ('low','high'):
  if r.get(k) is not None:finite(r[k])
 if r.get('low') is not None and value<r['low'] or r.get('high') is not None and value>r['high']:raise ValueError('共識超出高低預估')
 if r.get('low') is not None and r.get('high') is not None and r['low']>r['high']:raise ValueError('預估上下界倒置')
 if r.get('analyst_count') is not None and (isinstance(r['analyst_count'],bool) or not isinstance(r['analyst_count'],int) or r['analyst_count']<=0):raise ValueError('分析師家數無效')
 instant(r['published_at'])
 if r['forecast_as_of']:
  date.fromisoformat(r['forecast_as_of'])
  if r['forecast_as_of']>instant(r['published_at']).date().isoformat():raise ValueError('來源預估日不能晚於發布日')
 if r['eps_basis'] not in ('basic','diluted','normalized','not_disclosed') or r['share_basis'] not in ('current_ordinary_share_adjusted','as_reported','not_disclosed'):raise ValueError('每股口徑無效')
 return value
def estimate_batch(source_id,rows):
 prepared=[]
 with connect(True) as c:
  src=get_source(c,source_id)
  for r in rows:
   value=validate_estimate(r)
   if instant(r['published_at'])>instant(src['retrieved_at']):raise ValueError('預估發布時間在取得之後')
   if src['published_at'] and instant(r['published_at'])!=instant(src['published_at']):raise ValueError('預估發布時間與來源不符')
   qualified=r['eps_basis']!='not_disclosed' and r['share_basis']=='current_ordinary_share_adjusted' and bool(r['forecast_as_of']) and bool(r.get('basis_evidence'))
   payload={**r,'value':value,'calculation_ready':qualified,'vintage_type':'collected_document','original_live_snapshot':False}
   id=digest([source_id,payload]);prepared.append((id,source_id,r['symbol'],r['period_kind'],r['period'],r['statistic'],src['ingested_at'],r['published_at'],canonical(payload)))
  c.executemany('INSERT OR IGNORE INTO estimates VALUES(?,?,?,?,?,?,?,?,?)',prepared)
 return [r[0] for r in prepared]
def select_estimate(symbol,period,*,cutoff=None,statistic='median',provider=None,period_kind='calendar_year',eps_basis=None,share_basis=None):
 cutoff=instant(cutoff or now());rows=[]
 try:
  with connect() as c:
   rows=c.execute('SELECT e.*,s.provider AS source_provider,s.url AS source_url FROM estimates e JOIN sources s ON s.id=e.source_id WHERE e.symbol=? AND e.period=? AND e.statistic=? AND e.period_kind=?',(symbol,period,statistic,period_kind)).fetchall()
 except FileNotFoundError:return None
 eligible=[r for r in rows if instant(r['known_at'])<=cutoff and instant(r['published_at'])<=cutoff and (provider is None or r['source_provider']==provider) and (eps_basis is None or json.loads(r['payload'])['eps_basis']==eps_basis) and (share_basis is None or json.loads(r['payload'])['share_basis']==share_basis)]
 if not eligible:return None
 latest=max((instant(r['published_at']),instant(r['known_at'])) for r in eligible)
 tied=[r for r in eligible if (instant(r['published_at']),instant(r['known_at']))==latest]
 if len({(json.loads(r['payload'])['eps_basis'],json.loads(r['payload'])['share_basis']) for r in tied})>1:raise ValueError('同版本有多種EPS口徑，請明確指定')
 if len({json.loads(r['payload'])['value'] for r in tied})>1:raise ValueError('同來源版本存在衝突EPS值')
 r=max(tied,key=lambda r:r['id'])
 return {**json.loads(r['payload']),'id':r['id'],'source_id':r['source_id'],'known_at':r['known_at'],'source_url':r['source_url']}
def snapshot(universe_id,quote_ids,estimate_ids,*,notes=None):
 stamp=now()
 with connect(True) as c:
  u=c.execute('SELECT * FROM universes WHERE id=?',(universe_id,)).fetchone()
  if not u or instant(u['known_at'])>instant(stamp):raise ValueError('缺少成分股版本或尚未可用')
  for table,ids in [('quotes',quote_ids),('estimates',estimate_ids)]:
   for id in ids:
    row=c.execute(f'SELECT known_at FROM {table} WHERE id=?',(id,)).fetchone()
    if not row or instant(row[0])>instant(stamp):raise ValueError('快照參照缺漏或未來觀測')
  p={'universe_id':universe_id,'quote_ids':quote_ids,'estimate_ids':estimate_ids,'notes':notes or [],'no_valuation_computed':True}
  id=uuid.uuid4().hex;c.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(id,stamp,VERSION,canonical(p)))
 return id
def audit(payload):
 stamp=now();id=uuid.uuid4().hex
 with connect(True) as c:c.execute('INSERT INTO audits VALUES(?,?,?)',(id,stamp,canonical(payload)))
 return id
def read_snapshot(id=None):
 try:
  with connect() as c:
   row=c.execute('SELECT * FROM snapshots '+('WHERE id=?' if id else 'ORDER BY created_at DESC LIMIT 1'),(id,) if id else ()).fetchone()
   if not row:return None
   return {**dict(row),'payload':json.loads(row['payload'])}
 except FileNotFoundError:return None
def import_csv(path,source_id):
 with Path(path).open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
 for r in rows:
  for k in ('value','low','high'):r[k]=finite(r[k]) if r.get(k) else None
  r['analyst_count']=int(r['analyst_count']) if r.get('analyst_count') else None
  if r.get('basis_evidence'):r['basis_evidence']={'reference':r['basis_evidence']}
  if not r.get('forecast_as_of'):r['forecast_as_of']=None
 return estimate_batch(source_id,rows)
if __name__=='__main__':
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True);sub.add_parser('init');sub.add_parser('snapshot');s=sub.add_parser('import-eps');s.add_argument('--csv',required=True);s.add_argument('--source-id',required=True);s=sub.add_parser('add-source');s.add_argument('--raw',required=True);s.add_argument('--provider',required=True);s.add_argument('--url',required=True);s.add_argument('--published-at');args=p.parse_args()
 if args.cmd=='init':initialize();print('schema ready')
 elif args.cmd=='snapshot':print(json.dumps(read_snapshot(),ensure_ascii=False))
 elif args.cmd=='add-source':print(json.dumps({'source_id':source('analyst_eps_export',args.provider,args.url,Path(args.raw).read_bytes(),published_at=args.published_at)}))
 else:print(json.dumps({'imported':len(import_csv(args.csv,args.source_id))}))
