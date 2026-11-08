"""Explicit evidence parsers for forward-PE source inventory, no network on import."""
from __future__ import annotations
import json,re,sqlite3
from pathlib import Path
from bs4 import BeautifulSoup
from . import forward_pe_store as S,margin_statistics

def parse_weights(raw):
 soup=BeautifulSoup(raw,'html.parser');text=soup.get_text(' ',strip=True)
 m=re.search(r'資料日期\s*[：:]\s*(\d{4})/(\d{1,2})/(\d{1,2})',text)
 if not m:raise ValueError('權重表沒有資料日期，不以取得日代替')
 effective_date=f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}'
 tables=[t for t in soup.find_all('table') if '市值佔' in t.get_text()]
 if len(tables)!=1:raise ValueError('權重表識別不唯一')
 rows=[]
 for tr in tables[0].find_all('tr'):
  cells=[c.get_text(' ',strip=True) for c in tr.find_all('td',recursive=False)]
  if not cells:continue
  if len(cells)!=8:raise ValueError('權重雙欄格式改變')
  for i in (0,4):
   rank,symbol,name,weight=cells[i:i+4]
   if not rank and not symbol:continue
   rows.append({'rank':int(rank.replace(',','')),'symbol':symbol.strip(),'name':name,'weight_pct':S.finite(weight.rstrip('%'))})
 rows.sort(key=lambda r:r['rank'])
 if not rows or rows[0]['rank']!=1:raise ValueError('權重表為空或缺少第一名')
 return effective_date,rows

def parse_cnyes_eps(raw):
 soup=BeautifulSoup(raw,'html.parser');titles=[h for h in soup.find_all('h1') if 'Factset' in h.get_text().replace('FactSet','Factset') and 'EPS預估' in h.get_text()]
 title=titles[0] if len(titles)==1 else None
 if not title or 'Factset' not in title.get_text().replace('FactSet','Factset'):raise ValueError('不是FactSet預估EPS報導')
 symbol=re.search(r'\((\d{4})-TW\)',title.get_text())
 if not symbol or 'EPS預估' not in title.get_text():raise ValueError('標題缺少預估EPS股票代碼')
 pub=soup.find('meta',property='article:published_time')
 if not pub:raise ValueError('報導缺少發布時間')
 published=S.instant(pub['content']).isoformat()
 # Select the table immediately after the exact EPS section, never the revenue
 # table or the erroneous historical "EPS" amounts in the same article.
 heading=next((h for h in soup.find_all(['h2','h3','h4','p']) if h.get_text(' ',strip=True)=='市場預估EPS'),None)
 if not heading:raise ValueError('缺少市場預估EPS段落')
 table=heading.find_next('table')
 if table is None:raise ValueError('缺少預估EPS表格')
 trs=[[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'],recursive=False)] for tr in table.find_all('tr')]
 header=trs[0];years=[]
 for cell in header[1:]:
  m=re.fullmatch(r'(20\d{2})年(?:\(前值\))?',cell)
  if not m:raise ValueError('預估EPS年度欄位不明')
  years.append(m[1])
 if not years or len(set(years))!=len(years):raise ValueError('預估年度重複')
 values={};previous={}
 for row in trs[1:]:
  if len(row)!=len(header):raise ValueError('預估EPS表格欄位數不一致')
  label=row[0]
  if label not in ('最高值','最低值','平均值','中位數'):raise ValueError('預估EPS統計欄位不符')
  vals=[];priors=[]
  for cell in row[1:]:
   m=re.fullmatch(r'(-?[\d,]+(?:\.\d+)?)(?:\((-?[\d,]+(?:\.\d+)?)\))?',cell)
   if not m:raise ValueError('預估EPS缺值或格式不符')
   vals.append(S.finite(m[1].replace(',','')));priors.append(S.finite(m[2].replace(',','')) if m[2] else None)
  values[label]=vals;previous[label]=priors
 if set(values)!=set(('最高值','最低值','平均值','中位數')):raise ValueError('EPS統計欄位不足')
 # The analyst count in the paragraph describes the headline year only.
 text=soup.get_text(' ',strip=True);count=re.search(r'共\s*(\d+)位分析師.*?做出\s*(20\d{2})年EPS預估',text)
 rows=[]
 for i,year in enumerate(years):
  for label,statistic in [('中位數','median'),('平均值','mean')]:
   r={'symbol':symbol[1],'period_kind':'calendar_year','period':year,'statistic':statistic,'value':values[label][i],'low':values['最低值'][i],'high':values['最高值'][i],'analyst_count':int(count[1]) if count and year==count[2] else None,'currency':'TWD','currency_evidence':'TW股票新聞之元；同文營收標新台幣，EPS幣別待供應商獨立確認','eps_basis':'not_disclosed','share_basis':'not_disclosed','forecast_as_of':None,'published_at':published,'provider':'FactSet via Anue public news','estimate_type':'analyst_consensus','reported_prior_value':previous[label][i],'reported_prior_note':'同篇文所載前值，不另建較早保存紀錄','provider_snapshot_time_known':False,'title':title.get_text(' ',strip=True)}
   S.validate_estimate(r);rows.append(r)
 return published,rows

def local_quotes(symbols,day=None):
 """Read saved official per-day quotes/shares without opening a writable database."""
 path=(margin_statistics.folder()/'sources.sqlite3').resolve()
 if not path.exists():raise ValueError('缺少官方行情與發行股數保存庫')
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row;c.execute('BEGIN')
  if day is None:day=c.execute("SELECT MAX(day) FROM (SELECT day FROM latest WHERE market='TWSE' AND kind IN ('shares','quotes') GROUP BY day HAVING COUNT(*)=2)").fetchone()[0]
  if not day:raise ValueError('缺少同日股價與發行股數')
  refs={r['kind']:dict(r) for r in c.execute("SELECT s.* FROM latest l JOIN snapshots s ON s.id=l.snapshot_id WHERE l.market='TWSE' AND l.day=? AND l.kind IN ('quotes','shares')",(day,))}
  if len(refs)!=2:raise ValueError('兩種來源日期未對齊')
  rows=[dict(r) for r in c.execute('SELECT q.symbol,q.name,q.close,s.shares FROM quotes q JOIN shares s USING(symbol) WHERE q.snapshot_id=? AND s.snapshot_id=?',(refs['quotes']['id'],refs['shares']['id'])) if r['symbol'] in set(symbols)]
  c.rollback()
 result=[]
 for r in rows:
  if r['close'] is None or r['shares'] is None:continue
  result.append({**r,'market_date':day,'currency':'TWD','price_basis':'raw_close','shares_unit':'shares','shares_basis':'official_issued_shares','source_snapshot_ids':[refs['quotes']['id'],refs['shares']['id']]})
 return day,result,refs
