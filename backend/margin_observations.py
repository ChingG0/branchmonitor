"""Observable financing state and explicitly labelled two-method estimates.
Trading-signal adoption remains independent of this observational display.
"""
from pathlib import Path
from contextlib import closing
import sqlite3,json,gzip,math
from . import margin_statistics as src,margin_analytics as labels,margin_principal_repair as e
VERSION='margin-observations-1'
KEYS=('a','b')
def finite(x):return isinstance(x,(int,float)) and math.isfinite(x)
def positive(x):return finite(x) and x>0
def span(values):
 a=[v for v in values if finite(v)]
 return [min(a),max(a)] if a else None
def priced(r):return all(finite(r.get('loan_'+k)) and r['loan_'+k]>=0 and (r['quantity']==0 or r['loan_'+k]>0) and r.get('unpriced_'+k,0)<=e.EPS for k in KEYS)
def marked(r):return r['quantity']==0 or positive(r.get('close'))
def midpoint(r):return (r['loan_a']+r['loan_b'])/2 if priced(r) else None

def aggregate(rows,etf=False):
 active=[r for r in rows if r['quantity']>0];cost=[r for r in active if priced(r)];pool=[r for r in cost if marked(r)]
 missing_cost=len(active)-len(cost);missing_price=len(cost)-len(pool);methods=[]
 for key in KEYS:
  loans=sum(r['loan_'+key] for r in cost);classified=sum(r['loan_'+key] for r in pool);mv=sum(r['quantity']*1000*r['close'] for r in pool);amount=[0.]*5;counts=[0]*5
  for r in pool:
   i=e.bucket(r['quantity']*1000*r['close']/r['loan_'+key]);amount[i]+=r['loan_'+key];counts[i]+=1
  methods.append({'loan':loans if cost or not active else None,'classified_loan':classified,'maintenance':mv/classified if classified else None,'below_amount':amount[0] if pool or not active else None,'below_count':counts[0] if pool or not active else None,'amounts':amount,'counts':counts,'shares':[x/classified for x in amount] if classified else [None]*5})
 bins=[]
 for i,label in enumerate(labels.BANDS):
  available=bool(pool) or not active
  bins.append({'index':i,'label':label,'amount':span([m['amounts'][i] for m in methods]) if available else None,'share':span([m['shares'][i] for m in methods]) if pool else None,'count':None if etf or not available else span([m['counts'][i] for m in methods])})
 maintenance=span([m['maintenance'] for m in methods]);cross=sum(e.bucket(r['quantity']*1000*r['close']/r['loan_a'])!=e.bucket(r['quantity']*1000*r['close']/r['loan_b']) for r in pool)
 return {'active_names':len(active),'cost_names':len(cost),'classified_names':len(pool),'missing_cost_names':missing_cost,'missing_price_names':missing_price,'pending_names':missing_cost+missing_price,'etf':etf,
  'loan':span([m['loan'] for m in methods]),'maintenance':maintenance,'buffer':span([1-1.3/m for m in maintenance]) if maintenance else None,
  'below130_amount':span([m['below_amount'] for m in methods]),'below130_count':None if etf else span([m['below_count'] for m in methods]),'bins':bins,'cross_band_names':cross,
  'initial_assumption_names':sum(max(r['unknown_a'],r['unknown_b'])>0 for r in cost),'cost_filled_names':sum(bool(r.get('filled_a') or r.get('filled_b')) for r in cost),'range_kind':'two_method_estimate_not_confidence_interval','method_maintenance':[m['maintenance'] for m in methods]}

def comparison(rows,base,members=None):
 empty={'amount':None,'pct':None,'compared_names':0,'requested_names':len(members) if members is not None else None}
 if base is None:return empty
 now={r['symbol']:r for r in rows};old={r['symbol']:r for r in base};symbols=set(members) if members is not None else set(now)|set(old)
 common=[s for s in symbols if s in now and s in old and priced(now[s]) and priced(old[s])];changes=[];ratios=[]
 if not common:return {**empty,'requested_names':len(symbols)}
 for k in KEYS:
  n=sum(now[s]['loan_'+k] for s in common);b=sum(old[s]['loan_'+k] for s in common);changes.append(n-b);ratios.append(n/b-1 if b>0 else None)
 return {'amount':span(changes),'pct':span(ratios),'compared_names':len(common),'requested_names':len(symbols)}

def select(rows,base20,rs,market):
 stocks=[r for r in rows if r['category'] in ('listed_stock','otc_stock') and r['quantity']>0];by={r['symbol']:r for r in stocks}
 debt=sorted([r for r in stocks if priced(r)],key=lambda r:(-midpoint(r),r['symbol']))[:30]
 increases=[]
 if base20 is not None:
  old={r['symbol']:r for r in base20}
  for r in stocks:
   b=old.get(r['symbol'])
   if priced(r) and b and priced(b):
    delta=midpoint(r)-midpoint(b)
    if delta>0:increases.append((delta,r))
  increases=sorted(increases,key=lambda p:(-p[0],p[1]['symbol']))[:30]
 def group(key):return [by[r['symbol']] for r in rs.get('groups',{}).get(key,[]) if r.get('market')==market and r.get('symbol') in by] if rs else None
 strength=group('20');ytd=None
 if rs is not None:
  ytd=[by[r['symbol']] for r in rs.get('groups',{}).get('ytd',[]) if r.get('market')==market and r.get('symbol') in by and finite(r.get('excess_pct')) and r['excess_pct']>0]
 return {'debt30':debt,'increase30':[r for _,r in increases] if base20 is not None else None,'rs30':strength[:30] if strength is not None else None,'ytd':ytd}

def stock_row(r,old20):
 p=midpoint(r);m=[r['quantity']*1000*r['close']/r['loan_'+k] for k in KEYS] if priced(r) and marked(r) and r['quantity']>0 else []
 bands=[e.bucket(v) for v in m];change=comparison([r],[old20] if old20 is not None else None,[r['symbol']])
 return {'symbol':r['symbol'],'name':r['name'],'market':r['market'],'quantity':r['quantity'],'close':r['close'],'loan':span([r['loan_a'],r['loan_b']]) if priced(r) else None,'loan_mid':p,'maintenance':span(m),'buffer':span([1-1.3/v for v in m]),'band_min':min(bands) if bands else None,'band_max':max(bands) if bands else None,'change20':change,'initial_assumption':max(r['unknown_a'],r['unknown_b'])>0,'cost_available':priced(r),'cost_filled':bool(r.get('filled_a') or r.get('filled_b'))}

def official_market(rows,official,old_official,market):
 active=[r for r in rows if r['quantity']>0];valid=[r for r in active if marked(r)];mv=sum(r['quantity']*1000*r['close'] for r in valid)
 stocks=[r for r in rows if r['category'] in ('listed_stock','otc_stock')]
 return {'market':market,'official_loan':official,'official_maintenance':mv/official if valid and positive(official) else None,'maintenance_lower_bound':len(valid)<len(active),'missing_close_names':len(active)-len(valid),'market_value':mv if valid else None,'derived_label':'官方推導','change20_amount':official-old_official if official is not None and old_official is not None else None,'change20_pct':official/old_official-1 if official is not None and positive(old_official) else None,'stock_risk':aggregate(stocks),'includes_ETF':True}

def ro(p):
 c=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

def report(day=None,category='listed_stock',market='TWSE',*,store=None):
 categories={**labels.LABELS,'all_etf':'ETF全部（含主題與債券）'}
 if category not in categories:raise ValueError('無效資產分類')
 if market not in ('TWSE','TPEX'):raise ValueError('無效市場')
 path=(Path(store) if store else src.folder())/'observations.sqlite3'
 if not path.exists():return {'version':VERSION,'status':'missing','model_ready':False,'dates':[],'markets':[],'groups':[],'items':[]}
 with closing(ro(path)) as c:
  c.execute('BEGIN');meta=json.loads(c.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()[0])
  if meta['version']!=VERSION or meta['model_ready'] is not False:raise ValueError('觀察資料版本不符')
  dates=[r[0] for r in c.execute('SELECT day FROM calendar ORDER BY day')];chosen=day or dates[-1]
  if chosen not in dates:raise ValueError('該日不在觀察交易日曆內')
  i=dates.index(chosen);b20=dates[i-20] if i>=20 else None;b5=dates[i-5] if i>=5 else None
  def rows(d,m=None):return [dict(r) for r in c.execute('SELECT * FROM observations WHERE day=?'+(' AND market=?' if m else '')+' ORDER BY market,symbol',(d,m) if m else (d,))] if d else None
  current=rows(chosen);old20=rows(b20);old5=rows(b5);official={(r['day'],r['market']):r['official'] for r in c.execute('SELECT * FROM days WHERE day IN (?,?)',(chosen,b20))}
  availability={r['market']:bool(r['available']) for r in c.execute('SELECT market,available FROM days WHERE day=?',(chosen,))}
  markets=[]
  for m in ('TWSE','TPEX'):
   entry=official_market([r for r in current if r['market']==m],official.get((chosen,m)),official.get((b20,m)),m)|{'source_available':availability.get(m,False)}
   if not entry['source_available']:entry['stock_risk']=None
   markets.append(entry)
  matches=lambda r:r['symbol'].startswith('00') if category=='all_etf' else r['category']==category
  bucket=[r for r in current if matches(r)];before=[r for r in old20 if matches(r)] if old20 is not None else None
  selected=aggregate(bucket,category.startswith('etf_') or category=='all_etf');selected.update(key=category,label=categories[category],change20=comparison(bucket,before),source_missing_markets=[m for m in (('TWSE',) if category=='listed_stock' else ('TPEX',) if category=='otc_stock' else ('TWSE','TPEX')) if not availability.get(m)])
  selected['count_applicable']=category in ('listed_stock','otc_stock')
  if not selected['count_applicable']:
   selected['below130_count']=None
   for band in selected['bins']:band['count']=None
  r=c.execute('SELECT payload,saved_at FROM rs WHERE day=?',(chosen,)).fetchone();rs=json.loads(gzip.decompress(r['payload'])) if r else None
  if rs is not None and rs.get('as_of')!=chosen:raise ValueError('RS與查看日期不符')
  marketrows=[r for r in current if r['market']==market];base_market=[r for r in old20 if r['market']==market] if old20 is not None else None
  chosen_groups=select(marketrows,base_market,rs,market) if availability.get(market) else {k:None for k in labels.SUBSETS}
  groups=[]
  history_days=dates[max(0,i-29):i+1];history={d:[] for d in history_days}
  for r in c.execute('SELECT * FROM observations WHERE market=? AND day BETWEEN ? AND ? ORDER BY day,symbol',(market,history_days[0],chosen)):history[r['day']].append(dict(r))
  for key,label in labels.SUBSETS.items():
   members=chosen_groups[key];symbols=[r['symbol'] for r in members] if members is not None else [];symbolset=set(symbols);points=[]
   for d,dayrows in history.items():
    pool=[r for r in dayrows if r['symbol'] in symbolset];complete=bool(symbols) and len(pool)==len(symbols) and all(priced(r) and marked(r) for r in pool)
    a=aggregate(pool) if symbols and pool else None;points.append({'date':d,'maintenance':a['maintenance'] if a else None,'loan':a['loan'] if a else None,'covered_names':a['classified_names'] if a else 0,'requested_names':len(symbols),'complete':complete})
   summary=aggregate(members) if members is not None else None;delta20=comparison(marketrows,base_market,symbols) if members is not None else None
   prior5=[r for r in old5 if r['market']==market and r['symbol'] in symbolset] if old5 is not None else None;current5=aggregate(members) if members is not None else None;old_metrics=aggregate(prior5) if prior5 is not None and len(prior5)==len(symbols) and all(priced(r) and marked(r) for r in prior5) else None
   change5=span([(current5['method_maintenance'][j]-old_metrics['method_maintenance'][j])*100 for j in (0,1)]) if current5 and members is not None and all(priced(r) and marked(r) for r in members) and current5['maintenance'] and old_metrics and old_metrics['maintenance'] else None
   whole=aggregate([r for r in marketrows if r['category'] in ('listed_stock','otc_stock')]);difference=span([(summary['method_maintenance'][j]-whole['method_maintenance'][j])*100 for j in (0,1)]) if summary and summary['maintenance'] and whole['maintenance'] else None
   groups.append({'key':key,'label':label,'count':len(symbols) if members is not None else None,'metrics':summary,'change20':delta20,'change5_pp':change5,'market_difference_pp':difference,'members':[stock_row(r,None) for r in members] if members is not None else None,'points':points,'status':'estimated' if members is not None else 'missing_source','membership_rule':'兩法中值固定成員' if key in ('debt30','increase30') else '同日RS、有融資普通股交集','rs_date':chosen if rs is not None and key in ('rs30','ytd') else None})
  byold={r['symbol']:r for r in before} if before is not None else {};items=[stock_row(r,byold.get(r['symbol'])) for r in bucket if r['quantity']>0];items.sort(key=lambda r:(r['loan_mid'] is None,-(r['loan_mid'] or 0),r['symbol']))
 return {'version':VERSION,'status':'observational','model_ready':False,'trading_signals_enabled':False,'dates':dates,'as_of':chosen,'source_as_of':dates[-1],'created_at':meta['created_at'],'baseline20':b20,'baseline5':b5,'markets':markets,'categories':[{'key':k,'label':v} for k,v in categories.items()],'selected':selected,'cohort_market':market,'groups':groups,'items':items,'chart_membership':'固定查閱日成員回看30交易日，不是前瞻回測','range_note':'平均成本／FIFO的估算差異；非真實上下限或信賴區間。缺成本、市價的部位另外列待分級。','vintage':'本次回溯取得版本，非當時原始發布版本','engine_version':meta.get('cost_version',e.VERSION),'strict_engine_version':e.VERSION}
