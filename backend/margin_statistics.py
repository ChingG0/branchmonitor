"""Package 1: versioned public margin/quote source inventory. No loan model."""
from pathlib import Path
from datetime import datetime,date
from zoneinfo import ZoneInfo
import sqlite3,json,gzip,hashlib,os,math
from contextlib import contextmanager
from . import db
TZ=ZoneInfo('Asia/Taipei');VERSION='margin-statistics-sources-1';NORMALIZER='margin-sources-normalizer-2';START='2023-11-01'
KINDS={'credit':'融資流量與官方金額','quotes':'收盤與成交資訊','shares':'歷史發行股數'}
BUCKETS={'listed_stock':'上市個股','otc_stock':'上櫃個股','etf_cap':'市值型ETF','etf_dividend':'高股息ETF','etf_leverage':'槓桿／反向ETF','other':'其他／待分類'}
def folder():return db.DATA/'margin_statistics'
def now():return datetime.now(TZ).isoformat(timespec='seconds')
@contextmanager
def connect(store=None,write=False):
 p=(Path(store) if store else folder())/'sources.sqlite3'
 if write:
  p.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(p,timeout=30);c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON')
 else:c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=15)
 c.row_factory=sqlite3.Row
 try:
  yield c
  if write:c.commit()
 except Exception:
  if write:c.rollback()
  raise
 finally:c.close()
def initialize(store=None):
 with connect(store,True) as c:c.executescript('''
 CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,market TEXT,kind TEXT,day TEXT,sha256 TEXT,url TEXT,retrieved_at TEXT,cache_observed_at TEXT,row_count INTEGER,quality TEXT,raw_path TEXT);
 CREATE TABLE IF NOT EXISTS latest(market TEXT,kind TEXT,day TEXT,snapshot_id TEXT,PRIMARY KEY(market,kind,day));
 CREATE TABLE IF NOT EXISTS calendar(day TEXT PRIMARY KEY,snapshot_id TEXT);
 CREATE TABLE IF NOT EXISTS credit(snapshot_id TEXT,symbol TEXT,name TEXT,buy INTEGER,sell INTEGER,repay INTEGER,previous INTEGER,balance INTEGER,short_previous INTEGER,short_sell INTEGER,short_buy INTEGER,short_repay INTEGER,short_balance INTEGER,offset INTEGER,notes TEXT,reconcile_delta INTEGER,PRIMARY KEY(snapshot_id,symbol));
 CREATE INDEX IF NOT EXISTS credit_symbol ON credit(symbol,snapshot_id);
 CREATE TABLE IF NOT EXISTS totals(snapshot_id TEXT PRIMARY KEY,loan_previous INTEGER,loan_buy INTEGER,loan_sell INTEGER,loan_repay INTEGER,loan_amount INTEGER,units_previous INTEGER,units_balance INTEGER,short_units INTEGER);
 CREATE TABLE IF NOT EXISTS quotes(snapshot_id TEXT,symbol TEXT,name TEXT,close REAL,open REAL,high REAL,low REAL,volume REAL,turnover REAL,shares REAL,PRIMARY KEY(snapshot_id,symbol));
 CREATE TABLE IF NOT EXISTS shares(snapshot_id TEXT,symbol TEXT,name TEXT,shares REAL,isin TEXT,PRIMARY KEY(snapshot_id,symbol));
 CREATE TABLE IF NOT EXISTS extras(id TEXT PRIMARY KEY,kind TEXT,market TEXT,period TEXT,payload TEXT,url TEXT,retrieved_at TEXT,sha256 TEXT);
 CREATE TABLE IF NOT EXISTS failures(market TEXT,kind TEXT,day TEXT,error TEXT,checked_at TEXT,PRIMARY KEY(market,kind,day));
 ''')
def atomic(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name('.'+p.name+'.'+str(os.getpid())+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,allow_nan=False));t.replace(p)
def number(v,integer=False,nullable=False):
 s=str(v).replace(',','').strip()
 if nullable and s in ['','--','---','----','—','N/A','None']:return None
 try:n=float(s)
 except (ValueError,TypeError):raise ValueError('數字欄位無效')
 if not math.isfinite(n) or n<0 or integer and n!=int(n):raise ValueError('數字欄位範圍無效')
 return int(n) if integer else n
def check(value,day):
 if str(value.get('stat','')).lower()!='ok' or value.get('date')!=day.replace('-',''):raise ValueError('官方資料日期或狀態不符，未當成零值')
def parse_credit(v,market,day):
 check(v,day);tables=v.get('tables') or [];rows=[]
 if market=='TWSE':
  summary=next((t for t in tables if t.get('fields',[None])[0]=='項目'),None);detail=next((t for t in tables if t.get('fields',[None])[0]=='代號'),None)
  if not summary or not detail or detail['fields'][:7]!=['代號','名稱','買進','賣出','現金償還','前日餘額','今日餘額']:raise ValueError('上市融資欄位改變')
  totals={r[0]:r for r in summary['data']};units=totals['融資(交易單位)'];loan=totals['融資金額(仟元)'];short=totals['融券(交易單位)'];source=detail['data']
  for r in source:
   if len(r)!=len(detail['fields']):raise ValueError('上市融資列不完整')
   q=[number(r[i],True) for i in [2,3,4,5,6,11,9,8,10,12,14]]
   rows.append([str(r[0]).strip(),str(r[1]).strip(),*q,str(r[15]).strip(),q[3]+q[0]-q[1]-q[2]-q[4]])
  total={'loan_previous':number(loan[4],True)*1000,'loan_buy':number(loan[1],True)*1000,'loan_sell':number(loan[2],True)*1000,'loan_repay':number(loan[3],True)*1000,'loan_amount':number(loan[5],True)*1000,'units_previous':number(units[4],True),'units_balance':number(units[5],True),'short_units':number(short[5],True)}
 else:
  detail=next((t for t in tables if '前資餘額(張)' in t.get('fields',[])),None)
  if not detail:raise ValueError('上櫃融資欄位改變')
  for r in detail['data']:
   if len(r)!=len(detail['fields']):raise ValueError('上櫃融資列不完整')
   q=[number(r[i],True) for i in [3,4,5,2,6,10,11,12,13,14,18]]
   rows.append([str(r[0]).strip(),str(r[1]).strip(),*q,str(r[19]).strip(),q[3]+q[0]-q[1]-q[2]-q[4]])
  summary={r[1]:r for r in detail.get('summary',[])};units=summary['合計(張)'];loan=summary['融資金(仟元)']
  total={'loan_previous':number(loan[2],True)*1000,'loan_buy':number(loan[3],True)*1000,'loan_sell':number(loan[4],True)*1000,'loan_repay':number(loan[5],True)*1000,'loan_amount':number(loan[6],True)*1000,'units_previous':number(units[2],True),'units_balance':number(units[6],True),'short_units':number(units[14],True)}
 if len({r[0] for r in rows})!=len(rows) or not rows:raise ValueError('融資代碼重複或空表')
 if sum(r[6] for r in rows)!=total['units_balance']:raise ValueError('個股餘額加總與官方總張數不符')
 if abs(total['loan_previous']+total['loan_buy']-total['loan_sell']-total['loan_repay']-total['loan_amount'])>2000:raise ValueError('官方融資金額流量差超過千元欄位捨入容許2千元')
 return rows,total
def parse_quotes(v,market,day):
 check(v,day);tables=v.get('tables') or []
 table=next((t for t in tables if (market=='TWSE' and '每日收盤行情' in t.get('title','') or market=='TPEX' and t.get('title')=='上櫃股票行情') and t.get('data')),None)
 if not table:raise ValueError('找不到個股行情')
 fs=table['fields'];index={f:i for i,f in enumerate(fs)};code='證券代號' if market=='TWSE' else '代號';name='證券名稱' if market=='TWSE' else '名稱'
 names=['收盤價','開盤價','最高價','最低價','成交股數','成交金額'] if market=='TWSE' else ['收盤','開盤','最高','最低','成交股數','成交金額(元)']
 if not all(k in index for k in [code,name,*names]):raise ValueError('行情欄位改變')
 result=[]
 for r in table['data']:
  symbol=str(r[index[code]]).strip()
  # Retain ordinary stocks, ETFs/ETNs and nonstandard residual securities,
  # exclude warrants only. Category ambiguity is preserved, not guessed.
  if not (len(symbol)==4 or symbol.startswith(('00','01','02')) or len(symbol)==6 and symbol.isdigit() and symbol.startswith('91')):continue
  vals=[number(r[index[k]],nullable=True) for k in names];shares=number(r[index['發行股數']],nullable=True) if '發行股數' in index else None
  result.append([symbol,str(r[index[name]]).strip(),*vals,shares])
 if len({r[0] for r in result})!=len(result) or not result:raise ValueError('行情代碼重複或空表')
 return result
def parse_shares(v,day):
 check(v,day);fs=v.get('fields',[])
 if not all(k in fs for k in ['證券代號','證券名稱','發行股數','國際證券編碼']):raise ValueError('歷史發行股數欄位改變')
 rows=[[str(r[fs.index('證券代號')]).strip(),str(r[fs.index('證券名稱')]).strip(),number(r[fs.index('發行股數')],nullable=True),str(r[fs.index('國際證券編碼')])] for r in v.get('data',[])]
 if not rows or len({r[0] for r in rows})!=len(rows):raise ValueError('發行股數空表或代碼重複')
 return rows
def save_snapshot(v,market,kind,day,url,raw,retrieved_at,*,store=None,cache_observed_at=None):
 target=Path(store) if store else folder();digest=hashlib.sha256(raw).hexdigest();identity=hashlib.sha256((market+kind+day+digest+NORMALIZER).encode()).hexdigest();rel='raw/'+digest+'.json.gz';path=target/rel;path.parent.mkdir(parents=True,exist_ok=True)
 if kind=='credit':rows,total=parse_credit(v,market,day)
 elif kind=='quotes':rows=parse_quotes(v,market,day)
 elif kind=='shares':rows=parse_shares(v,day)
 else:raise ValueError('無效資料類型')
 if not path.exists():
  tmp=path.with_suffix('.'+str(os.getpid())+'.tmp');tmp.write_bytes(gzip.compress(raw));tmp.replace(path)
 quality=('retrospective_source_version' if day<retrieved_at[:10] else 'preliminary_current_day')+'/'+NORMALIZER
 with connect(target,True) as c:
  c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,market,kind,day,digest,url,retrieved_at,cache_observed_at,len(rows),quality,rel))
  if kind=='credit':
   c.executemany('INSERT OR IGNORE INTO credit VALUES('+','.join('?'*16)+')',[[identity,*r] for r in rows]);c.execute('INSERT OR IGNORE INTO totals VALUES('+','.join('?'*9)+')',(identity,*[total[k] for k in ['loan_previous','loan_buy','loan_sell','loan_repay','loan_amount','units_previous','units_balance','short_units']]))
  elif kind=='quotes':c.executemany('INSERT OR IGNORE INTO quotes VALUES('+','.join('?'*10)+')',[[identity,*r] for r in rows])
  else:c.executemany('INSERT OR IGNORE INTO shares VALUES(?,?,?,?,?)',[[identity,*r] for r in rows])
  c.execute('INSERT OR REPLACE INTO latest VALUES(?,?,?,?)',(market,kind,day,identity));c.execute('DELETE FROM failures WHERE market=? AND kind=? AND day=?',(market,kind,day))
 return identity,len(rows)
def classify(symbol,name,market):
 if len(symbol)==4 and symbol.isdigit() and not symbol.startswith(('0','91')) and '-DR' not in name.upper():return 'listed_stock' if market=='TWSE' else 'otc_stock'
 if not symbol.startswith('00'):return 'other'
 if symbol.endswith(('L','R')) or '正2' in name or '反1' in name or '反2' in name:return 'etf_leverage'
 if symbol.endswith('B') or any(x in name for x in ['債','REIT','不動產','收益']):return 'other'
 if any(x in name for x in ['高股息','高息','優息','股利']):return 'etf_dividend'
 if symbol in ['0050','0051','006203','006204','006208','006201']:return 'etf_cap'
 return 'other'
def job(store=None):
 try:
  v=json.loads(((Path(store) if store else folder())/'job.json').read_text())
  if v.get('status')=='running' and v.get('pid'):
   try:os.kill(v['pid'],0)
   except ProcessLookupError:return {**v,'status':'interrupted','message':'上次回補中斷，可續接既有資料。'}
  return v
 except (OSError,ValueError):return {'status':'idle','message':'尚未建立回補作業'}
def principal_validation(store=None):
 try:
  root=Path(store) if store else folder();path=root/'principal-validation-repair.json'
  if not path.exists():path=root/'principal-validation.json'
  value=json.loads(path.read_text())
  return value if value.get('version') in ('margin-principal-diagnostic-1','margin-principal-repair-2','margin-principal-repair-3') and value.get('model_ready') is False and value.get('status')=='not_accepted' else None
 except (OSError,ValueError,TypeError,AttributeError):return None
def report(market='TWSE',day=None,search='',page=1,*,store=None):
 if market not in ['TWSE','TPEX']:raise ValueError('無效市場')
 page=max(1,min(int(page),1000));search=str(search)[:40];target=Path(store) if store else folder();validation=principal_validation(target);scope=job(target);scope_start=scope.get('start',START);scope_end=scope.get('end','9999-12-31');empty={'version':VERSION,'phase':2 if validation else 1,'principal_validation':validation,'as_of':None,'markets':[],'coverage':[],'baskets':[],'dates':[],'items':[],'total_items':0,'page':page,'job':job(target),'model_ready':False,'blocked_reason':'包2尚未具備所有放行條件，維持率分布與訊號維持未啟用。' if validation else '包1僅建立來源與分類；包2本金、暖機與敏感度尚未驗收，不顯示維持率分布或訊號。'}
 if not (target/'sources.sqlite3').exists():return empty
 with connect(target) as c:
  dates=[r[0] for r in c.execute("SELECT DISTINCT day FROM latest WHERE kind='credit' AND market=? AND day BETWEEN ? AND ? ORDER BY day DESC",(market,scope_start,scope_end))];chosen=day or (dates[0] if dates else None)
  if day and day not in dates:raise ValueError('該日尚無已核對融資資料')
  expected=[r[0] for r in c.execute('SELECT day FROM calendar WHERE day BETWEEN ? AND ? ORDER BY day',(scope_start,scope_end))];coverage=[];markets=[];common=set(expected)
  for m in ['TWSE','TPEX']:
   for kind in (['credit','quotes','shares'] if m=='TWSE' else ['credit','quotes']):
    ds=[r[0] for r in c.execute('SELECT day FROM latest WHERE market=? AND kind=? AND day BETWEEN ? AND ? ORDER BY day',(m,kind,scope_start,scope_end))];present=set(ds);common.intersection_update(present);missing=[d for d in expected if d not in present]
    coverage.append({'market':m,'kind':kind,'label':KINDS[kind],'first':ds[0] if ds else None,'last':ds[-1] if ds else None,'days':len(ds),'expected_days':len(expected),'missing_days':len(missing),'missing_sample':missing[:5]})
   totals=c.execute("SELECT l.day,t.*,s.retrieved_at FROM latest l JOIN totals t ON t.snapshot_id=l.snapshot_id JOIN snapshots s ON s.id=l.snapshot_id WHERE l.market=? AND l.kind='credit' ORDER BY l.day DESC LIMIT 1",(m,)).fetchone();markets.append({'market':m,**(dict(totals) if totals else {'day':None})})
  sid=c.execute("SELECT snapshot_id FROM latest WHERE market=? AND kind='credit' AND day=?",(market,chosen)).fetchone() if chosen else None;items=[];baskets=[];total_items=0
  if sid:
   qsid=c.execute("SELECT snapshot_id FROM latest WHERE market=? AND kind='quotes' AND day=?",(market,chosen)).fetchone();ssid=c.execute("SELECT snapshot_id FROM latest WHERE market=? AND kind='shares' AND day=?",(market,chosen)).fetchone();qsid=qsid[0] if qsid else '';ssid=ssid[0] if ssid else ''
   rows=c.execute('''SELECT c.*,q.close,q.shares AS quote_shares,s.shares AS official_shares FROM credit c LEFT JOIN quotes q ON q.snapshot_id=? AND q.symbol=c.symbol LEFT JOIN shares s ON s.snapshot_id=? AND s.symbol=c.symbol WHERE c.snapshot_id=? ORDER BY c.balance DESC,c.symbol''',(qsid,ssid,sid[0])).fetchall();classified={k:[] for k in BUCKETS}
   for source in rows:
    r=dict(source);r.pop('snapshot_id',None);r['bucket']=classify(r['symbol'],r['name'],market);r['bucket_label']=BUCKETS[r['bucket']];r['shares']=r.pop('official_shares') if market=='TWSE' else r.get('quote_shares');r.pop('quote_shares',None);r['market_cap']=r['shares']*r['close'] if r['shares'] is not None and r['shares']>0 and r['close'] is not None and r['close']>0 else None;r['ratio_status']='降成／資格註記待核對' if bool(r['notes']) else '一般上限60%；個別成數待核對';r['classification_status']='規則初分；非歷史成員認證' if r['bucket'].startswith('etf') or r['bucket']=='other' else '普通股代碼初分';classified[r['bucket']].append(r)
    if not search or search.lower() in (r['symbol']+' '+r['name']).lower():items.append(r)
   total_items=len(items);items=items[(page-1)*50:page*50]
   baskets=[{'key':k,'label':BUCKETS[k],'securities':len(v),'with_balance':sum(r['balance']>0 for r in v),'finance_units':sum(r['balance'] for r in v),'price_covered':sum(r['balance']>0 and r['close'] is not None for r in v),'cap_covered':sum(r['balance']>0 and r['market_cap'] is not None for r in v)} for k,v in classified.items() if k not in (['otc_stock'] if market=='TWSE' else ['listed_stock'])]
  revision_count=c.execute('SELECT COUNT(*) FROM snapshots WHERE day BETWEEN ? AND ?',(scope_start,scope_end)).fetchone()[0];mismatches=c.execute('SELECT COUNT(*) FROM credit c JOIN latest l ON l.snapshot_id=c.snapshot_id WHERE c.reconcile_delta!=0').fetchone()[0];failures=[dict(r) for r in c.execute('SELECT * FROM failures WHERE day BETWEEN ? AND ? ORDER BY checked_at DESC LIMIT 10',(scope_start[:7]+'-01',scope_end))];extras=[dict(r) for r in c.execute('SELECT kind,market,MIN(period) first,MAX(period) last,COUNT(*) records FROM extras GROUP BY kind,market')]
  return {**empty,'as_of':chosen,'market':market,'markets':markets,'coverage':coverage,'baskets':baskets,'dates':dates,'items':items,'total_items':total_items,'page':page,'source_versions':revision_count,'flow_mismatch_rows':mismatches,'failures':failures,'supplementary':extras,'common_complete_days':len(common),'calendar_days':len(expected),'calendar_start':expected[0] if expected else None,'calendar_end':expected[-1] if expected else None,'quality_note':('本金僅做診斷，未通過驗收。' if validation else '本金尚未推估。')+'張數為官方交易單位；ETF分類先按公開名稱及固定清單初分，其他類不硬併入三籃。歷史資料是本次回溯取得版本，不能當成當年原始發布版本。'}
